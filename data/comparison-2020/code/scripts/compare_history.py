#!/usr/bin/env python3
"""Full 2020+ embeddings, all-history static build, and 2024+ weekly replay."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import api_usage
import embed
import hn_sync
import production
import topics
import weekly

START=1577836800
SPLIT=1704067200
WEEK=7*86400


def save(path,value):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2))
    tmp.replace(path)


def connect(path):
    conn=sqlite3.connect(path,timeout=60)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=-32768')
    return conn


def prepare(source,out):
    manifest=out/'manifest.json'
    if manifest.exists():
        return json.loads(manifest.read_text())
    conn=connect(out/'corpus.db')
    conn.executescript(hn_sync.SCHEMA)
    conn.execute('ATTACH DATABASE ? AS original',(source.resolve().as_uri()+'?mode=ro',))
    with conn:
        conn.execute('''INSERT OR IGNORE INTO stories SELECT * FROM original.stories
            WHERE time>=? AND dead=0 AND deleted=0 AND title IS NOT NULL''',(START,))
    source_count=conn.execute('SELECT count(*) FROM stories').fetchone()[0]
    conn.create_function('subject_text',3,lambda title,url,text:embed.to_text(title,url,text,version=2))
    empty=[r[0] for r in conn.execute("SELECT id FROM stories WHERE trim(subject_text(title,url,text))=''")]
    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS excluded_stories AS SELECT * FROM stories WHERE 0')
        for ident in empty:
            conn.execute('INSERT INTO excluded_stories SELECT * FROM stories WHERE id=?',(ident,))
            conn.execute('DELETE FROM stories WHERE id=?',(ident,))
    counts=conn.execute('SELECT count(*),sum(time<?),sum(time>=?),max(time) FROM stories',(SPLIT,SPLIT)).fetchone()
    if not counts[0] or not counts[1] or not counts[2]:
        conn.close()
        raise ValueError('The comparison needs stories on both sides of 2024-01-01')
    conn.execute(embed.SCHEMA)
    columns={r[1] for r in conn.execute('PRAGMA table_info(embeddings)')}
    if 'input_version' not in columns:
        conn.execute('ALTER TABLE embeddings ADD COLUMN input_version INTEGER NOT NULL DEFAULT 2')
        conn.execute('ALTER TABLE embeddings ADD COLUMN input_hash TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS embedding_input ON embeddings(model,input_version,input_hash)')
    conn.commit()
    result=dict(source=str(source.resolve()),start=START,split=SPLIT,end=counts[3]+1,
                stories=counts[0],initial_stories=counts[1],streamed_stories=counts[2],
                created_at=int(time.time()),input_version=2,
                source_stories=source_count,excluded_empty_subjects=empty,
                caveat='Retrospective snapshot scores/edits/deletions and modern model knowledge, not an as-of historical archive.',
                criteria=dict(recent_assignment_coverage=.85,static_subject_recall=.90,
                              duplicate_fraction_max=.10,mean_coherence_min=4,
                              median_emerging_subject_delay_days_max=28))
    save(manifest,result)
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.close()
    print(json.dumps(result),flush=True)
    return result


def embed_all(out,workers=4):
    """Bounded concurrent API batches; SQLite writes remain on the main thread."""
    conn=connect(out/'corpus.db')
    start=time.monotonic();done=0;last=0
    existing=conn.execute('SELECT count(*) FROM embeddings').fetchone()[0]
    def persist(job):
        nonlocal done
        batch,groups,vectors,pending,future=job
        if pending:
            generated=future.result()
            if len(generated)!=len(pending) or any(len(v)!=embed.DIM*4 for v in generated):
                raise ValueError('Invalid embedding response')
            vectors.update(zip(pending,generated))
        conn.executemany('INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?)',
            [(i,embed.MODEL,vectors[k],2,k) for k,g in groups.items() for i in g['ids']])
        conn.commit();done+=len(batch)
        if done%10000==0:
            print(f'Embedded {existing+done:,} total ({done:,} this run); {done/max(1,time.monotonic()-start):.0f} stories/sec',flush=True)
            save(out/'embedding-progress.json',dict(total=existing+done,completed_this_run=done,last_id=batch[-1][0],elapsed=time.monotonic()-start))
    try:
        with ThreadPoolExecutor(workers) as pool:
            jobs=deque()
            while True:
                batch=conn.execute('''SELECT s.id,s.title,s.url,s.text FROM stories s LEFT JOIN embeddings e USING(id)
                    WHERE s.id>? AND e.id IS NULL ORDER BY s.id LIMIT 1000''',(last,)).fetchall()
                if not batch:
                    break
                last=batch[-1][0];groups={};vectors={};pending=[]
                for row in batch:
                    text=embed.to_text(*row[1:],version=2)
                    key=hashlib.sha256(text.encode()).hexdigest()
                    groups.setdefault(key,dict(text=text,ids=[]))['ids'].append(row[0])
                for key,g in groups.items():
                    cached=conn.execute('SELECT vec FROM embeddings WHERE model=? AND input_version=2 AND input_hash=? LIMIT 1',
                                        (embed.MODEL,key)).fetchone()
                    if cached:
                        vectors[key]=cached[0]
                    else:
                        pending.append(key)
                future=pool.submit(embed.embed_batch,[groups[k]['text'] for k in pending]) if pending else None
                jobs.append((batch,groups,vectors,pending,future))
                if len(jobs)>=workers:
                    persist(jobs.popleft())
            while jobs:
                persist(jobs.popleft())
        missing=conn.execute('SELECT count(*) FROM stories s LEFT JOIN embeddings e USING(id) WHERE e.id IS NULL').fetchone()[0]
        if missing:
            raise RuntimeError(f'{missing} embeddings missing')
        count=conn.execute('SELECT count(*) FROM embeddings').fetchone()[0]
        save(out/'embedding-progress.json',dict(complete=True,total=count,elapsed_this_run=time.monotonic()-start,finished_at=int(time.time())))
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        print(f'Embedding complete: {count:,}',flush=True)
    finally:
        conn.close()


def ingest(conn,lower,upper):
    with conn:
        conn.execute('INSERT OR IGNORE INTO stories SELECT * FROM archive.stories WHERE time>=? AND time<?',(lower,upper))
        conn.execute('''INSERT OR IGNORE INTO embeddings SELECT e.* FROM archive.embeddings e
            JOIN archive.stories s USING(id) WHERE s.time>=? AND s.time<?''',(lower,upper))


def init_db(out,name,end):
    path=out/f'{name}.db'
    conn=connect(path)
    conn.executescript(hn_sync.SCHEMA)
    conn.execute('''CREATE TABLE IF NOT EXISTS embeddings(id INTEGER PRIMARY KEY,model TEXT NOT NULL,
        vec BLOB NOT NULL,input_version INTEGER NOT NULL,input_hash TEXT)''')
    conn.execute('CREATE INDEX IF NOT EXISTS embedding_input ON embeddings(model,input_version,input_hash)')
    conn.execute('ATTACH DATABASE ? AS archive',((out/'corpus.db').resolve().as_uri()+'?mode=ro',))
    ingest(conn,START,end)
    return conn


def export_topics(conn,path,since=0):
    exported=[]
    for ident,name,description in conn.execute(
            "SELECT t.id,name,description FROM topics t JOIN topic_registry r ON r.id=t.id WHERE r.status='active'"):
        rows=conn.execute('''SELECT s.title,st.sim FROM story_topics st JOIN stories s USING(id)
            WHERE st.topic=? AND s.time>=? ORDER BY st.sim DESC LIMIT 30''',(ident,since)).fetchall()
        evidence_since=since
        if not rows and since:
            evidence_since=0
            rows=conn.execute('''SELECT s.title,st.sim FROM story_topics st JOIN stories s USING(id)
                WHERE st.topic=? ORDER BY st.sim DESC LIMIT 30''',(ident,)).fetchall()
        typical=list(dict.fromkeys(r[0] for r in rows))[:6]
        varied=[r[0] for r in conn.execute('''SELECT s.title FROM story_topics st JOIN stories s USING(id)
            WHERE st.topic=? AND s.time>=? ORDER BY (s.id*2654435761)%4294967296 LIMIT 6''',(ident,evidence_since))]
        count,first,last=conn.execute('''SELECT count(*),min(s.time),max(s.time) FROM story_topics st
            JOIN stories s USING(id) WHERE st.topic=? AND s.time>=?''',(ident,since)).fetchone()
        exported.append(dict(id=ident,name=name,description=description,typical=typical,varied=varied,
                             assigned_in_window=count,first_story_at=first,last_story_at=last,evidence_since=evidence_since))
    save(path,exported)


def build(out,name,end):
    print(f'Preparing {name} build through {datetime.fromtimestamp(end-1,timezone.utc).isoformat()}',flush=True)
    artifact=out/f'{name}.npz'
    production.MODEL_PATH=str(artifact.resolve())
    conn=init_db(out,name,end)
    conn.close()
    print(f'{name}: isolated input database ready',flush=True)
    if not artifact.exists():
        topics.main(str(out/f'{name}.db'),str(artifact),now=end-1)
    conn=connect(out/f'{name}.db')
    production.setup(conn,now=end-1)
    if not (out/f'{name}-initial-topics.json').exists():
        export_topics(conn,out/f'{name}-initial-topics.json')
    if not (out/'maps'/f'{name}-initial.json').exists():
        snapshot_map(conn,out,f'{name}-initial',end-1)
    conn.close()


def snapshot_map(conn,out,label,now):
    """Observe taxonomy and semantic neighbors without influencing maintenance."""
    directory=out/'maps';directory.mkdir(exist_ok=True)
    _,ids,centers=production.model(conn)
    similarities=centers@centers.T
    nodes=[]
    for i,ident in enumerate(ids):
        row=conn.execute('''SELECT t.name,t.description,t.size,t.x,t.y,r.born,r.parent
            FROM topics t JOIN topic_registry r ON r.id=t.id WHERE t.id=?''',(int(ident),)).fetchone()
        neighbors=[int(j) for j in np.argsort(-similarities[i]) if j!=i][:3]
        nodes.append(dict(id=int(ident),name=row[0],description=row[1],size=row[2],
            x=row[3],y=row[4],born=row[5],parent=row[6],neighbors=[
                dict(id=int(ids[j]),similarity=float(similarities[i,j])) for j in neighbors]))
    save(directory/f'{label}.json',dict(at=now,nodes=nodes,
        note='Top three semantic neighbors per topic; diagnostic relationships, not UI graph edges.'))
    np.savez_compressed(directory/f'{label}.npz',ids=ids,centers=centers)


def replay(out,end):
    production.MODEL_PATH=str((out/'stream.npz').resolve())
    conn=connect(out/'stream.db')
    conn.execute('ATTACH DATABASE ? AS archive',((out/'corpus.db').resolve().as_uri()+'?mode=ro',))
    checkpoint=out/'stream-progress.json'
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else dict(through=SPLIT,weeks=0)
    try:
        while state['through']<end:
            lower=state['through'];upper=min(lower+WEEK,end);now=upper-1
            t0=time.monotonic()
            ingest(conn,lower,upper)
            ingested=time.monotonic()
            weekly.classify_pending(conn,now=now)
            classified=time.monotonic()
            events=weekly.maintain(conn,now=now)
            snapshot_map(conn,out,f"week-{state['weeks']+1:03d}",now)
            record=dict(through=upper,date=datetime.fromtimestamp(now,timezone.utc).isoformat(),
                events=events,seconds=time.monotonic()-t0,
                stage_seconds=dict(ingest=ingested-t0,classify=classified-ingested,maintain=time.monotonic()-classified),
                topics=conn.execute("SELECT count(*) FROM topic_registry WHERE status='active'").fetchone()[0],
                assigned=conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],
                queued=conn.execute('SELECT count(*) FROM classification_queue').fetchone()[0])
            if events:
                record['taxonomy']=[dict(zip(('id','name','description','born'),r)) for r in conn.execute(
                    "SELECT t.id,name,description,born FROM topics t JOIN topic_registry r ON r.id=t.id WHERE r.status='active'")]
            with (out/'weeks.jsonl').open('a') as log:
                log.write(json.dumps(record)+'\n')
            state.update(through=upper,weeks=state['weeks']+1,last_week=record,complete=upper==end)
            save(checkpoint,state)
            print('WEEK '+json.dumps(record),flush=True)
    finally:
        conn.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=['embed','static','stream','all'])
    ap.add_argument('--out',type=Path,default=Path('data/comparison-2020'))
    ap.add_argument('--source',type=Path,default=Path('data/hackernews.db'))
    ap.add_argument('--max-usd',type=float,default=15)
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    temp=args.out/'tmp';temp.mkdir(exist_ok=True)
    os.environ['SQLITE_TMPDIR']=str(temp.resolve())
    # Static and stream builds may run independently after the corpus is complete.
    with (args.out/f'{args.stage}.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        api_usage.configure(str(args.out/'api-usage.db'),args.max_usd)
        manifest=prepare(args.source,args.out)
        if args.stage in ('embed','all'):
            embed_all(args.out)
        if args.stage in ('static','all'):
            if not json.loads((args.out/'embedding-progress.json').read_text()).get('complete'):
                raise ValueError('Finish all embeddings before building either model')
            build(args.out,'static',manifest['end'])
        if args.stage in ('stream','all'):
            if not json.loads((args.out/'embedding-progress.json').read_text()).get('complete'):
                raise ValueError('Finish all embeddings before building either model')
            build(args.out,'stream',SPLIT)
            replay(args.out,manifest['end'])


if __name__=='__main__':
    main()
