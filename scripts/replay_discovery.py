"""Replay only topic discovery/refinement, with no per-story semantic reviews.

Reuse the frozen initial model and embeddings. Reset the isolated experiment
database to its exact pre-2024 assignments, then run the actual discovery code.
"""
import fcntl
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

OUT=Path('data/comparison-2020')
sys.path.insert(0,str((OUT/'code').resolve()))
import api_usage
import production
import routing
import weekly
from topics import iter_vectors
from scripts.compare_history import connect,ingest,save,export_topics,SPLIT,WEEK


def reset(out):
    marker=out/'discovery-reset.json'
    if marker.exists():
        return
    initial=json.loads((out/'maps/stream-initial.json').read_text())['nodes']
    model=np.load(out/'stream.npz')
    conn=connect(out/'stream.db')
    try:
        print('Restoring the existing pre-2024 model; no new embeddings or naming calls',flush=True)
        with conn:
            conn.execute('CREATE TEMP TABLE future_ids AS SELECT id FROM stories WHERE time>=?',(SPLIT,))
            conn.execute('CREATE UNIQUE INDEX future_ids_id ON future_ids(id)')
            for table in ('story_topics','classification_queue','article_decisions','topic_candidates','topic_registry','topics','maintenance'):
                conn.execute(f'DELETE FROM {table}')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='topic_changes'").fetchone():
                conn.execute('DELETE FROM topic_changes')
            for table in ('embeddings','story_metadata','stories'):
                conn.execute(f'DELETE FROM {table} WHERE id IN (SELECT id FROM future_ids)')
            for n in initial:
                conn.execute('INSERT INTO topics VALUES (?,?,?,?,?,?,?)',
                    (n['id'],n['name'],n['description'],0,0,n['x'],n['y']))
                conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                    (n['id'],model['centroids'][n['id']].astype('<f4').tobytes(),SPLIT-1))
            conn.execute("INSERT INTO maintenance VALUES ('weekly',?)",(SPLIT-1,))
            count=0
            # Initial store() had no semantic overrides. Preserve its already
            # computed metadata and rebuild the same two assignment tables.
            for ids,vectors in iter_vectors(conn,'e.input_version=2 AND s.dead=0 AND s.deleted=0',size=8192):
                labels,fits,margins=routing.decisions(vectors,model['mean'],model['centroids'])
                accepted=[];pending=[]
                for ident,label,fit,margin in zip(ids,labels,fits,margins):
                    fit=float(fit);margin=float(margin)
                    reason='low_fit' if fit<routing.MIN_FIT else 'ambiguous' if margin<routing.MIN_MARGIN else None
                    row=(ident,int(label),float(fit),float(margin))
                    if reason:
                        pending.append((ident,reason,int(label),float(fit),float(margin),SPLIT-1))
                    else:
                        accepted.append(row)
                conn.executemany('INSERT INTO story_topics VALUES (?,?,?,?)',accepted)
                conn.executemany('''INSERT INTO classification_queue(id,reason,suggested_topic,sim,margin,updated_at)
                    VALUES (?,?,?,?,?,?)''',pending)
                count+=len(ids)
            actual=dict(conn.execute('SELECT topic,count(*) FROM story_topics GROUP BY topic'))
            assert actual=={n['id']:n['size'] for n in initial},'Initial assignment counts changed'
            assert conn.execute('SELECT count(*) FROM story_metadata').fetchone()[0]==count
            expected=json.loads((out/'manifest.json').read_text())['initial_stories']
            assert count==expected
            conn.execute('CREATE INDEX IF NOT EXISTS embeddings_routing ON embeddings(input_version,id)')
            conn.execute('CREATE INDEX IF NOT EXISTS story_topics_summary ON story_topics(topic,sim)')
            conn.execute('UPDATE topics SET size=(SELECT count(*) FROM story_topics st WHERE st.topic=topics.id), '
                         'cohesion=(SELECT coalesce(avg(sim),0) FROM story_topics st WHERE st.topic=topics.id)')
        save(marker,dict(stories=count,topics=len(initial),exact_initial_topic_counts_verified=True,
            reset_at=int(time.time()),scope='discovery only; per-story semantic reviews disabled'))
        print(f'Restored {len(initial)} topics and {count:,} initial stories; original topic counts match',flush=True)
    finally:
        conn.close()


def replay(out):
    production.MODEL_PATH=str((out/'stream.npz').resolve())
    conn=connect(out/'stream.db')
    conn.execute('ATTACH DATABASE ? AS archive',((out/'corpus.db').resolve().as_uri()+'?mode=ro',))
    end=json.loads((out/'manifest.json').read_text())['end']
    checkpoint=out/'discovery-progress.json'
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else dict(through=SPLIT,weeks=0)
    try:
        while state['through']<end:
            lower=state['through'];upper=min(lower+WEEK,end);now=upper-1;t0=time.monotonic()
            ingest(conn,lower,upper)
            weekly.classify_pending(conn,now=now)
            # Embedding assignments supply topic membership; only discovery,
            # centroid adaptation, and merge/split validation use maintenance.
            events=production.weekly(conn,now=now)
            record=dict(through=upper,weeks=state['weeks']+1,events=events,seconds=time.monotonic()-t0,
                topics=conn.execute("SELECT count(*) FROM topic_registry WHERE status='active'").fetchone()[0])
            if events:
                record['taxonomy']=[dict(zip(('id','name','description','born'),r)) for r in conn.execute(
                    "SELECT t.id,name,description,born FROM topics t JOIN topic_registry r ON r.id=t.id WHERE r.status='active'")]
            with (out/'discovery-weeks.jsonl').open('a') as log:
                log.write(json.dumps(record)+'\n')
            state.update(through=upper,weeks=record['weeks'],last_week=record,complete=upper==end)
            save(checkpoint,state)
            print(json.dumps(record),flush=True)
        export_topics(conn,out/'stream-discovery-topics.json',since=end-90*86400)
        print('Discovery-only replay complete',flush=True)
    finally:
        conn.close()


if __name__=='__main__':
    os.environ['SQLITE_TMPDIR']=str((OUT/'tmp').resolve())
    with (OUT/'stream.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        api_usage.configure(str(OUT/'api-usage.db'),15)
        reset(OUT)
        replay(OUT)
