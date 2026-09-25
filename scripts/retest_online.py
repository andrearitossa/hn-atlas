"""Frozen-code static vs weekly-online comparison, reusing all v2 embeddings."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/os.getenv('COMPARISON_RUN','comparison-online-v2')
OUT.mkdir(exist_ok=True)
CODE=OUT/'code'
if not CODE.exists():
    CODE.mkdir()
    for source in ROOT.glob('*.py'):
        shutil.copy2(source,CODE/source.name)
    (OUT/'code-manifest.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in CODE.glob('*.py')},indent=2))
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(CODE))
import api_usage
import production
import topics
import weekly
import structure  # Freeze the lazily imported maintainer before helper path changes.
import routing
from scripts.compare_history import connect,init_db,ingest,export_topics,save,SPLIT,WEEK

BASE=ROOT/'data/comparison-2020'
if not (OUT/'corpus.db').exists():
    (OUT/'corpus.db').symlink_to(BASE/'corpus.db')
(OUT/'tmp').mkdir(exist_ok=True)
os.environ['SQLITE_TMPDIR']=str(OUT/'tmp')
MANIFEST=json.loads((BASE/'manifest.json').read_text())
END=MANIFEST['end']


def progress(**fields):
    fields['at']=int(time.time())
    save(OUT/'progress.json',fields)
    print(json.dumps(fields),flush=True)


def build(name,end):
    artifact=OUT/f'{name}.npz';marker=OUT/f'{name}-ready.json'
    if marker.exists():
        return
    progress(stage='initial_build',which=name,through=end)
    conn=init_db(OUT,name,end);conn.close()
    restore=os.getenv('COMPARISON_SEED_FROM') if name=='stream' else None
    if restore:
        # Restore the exact original seed, never the interrupted online map.
        # Embeddings and fitted centers are reused; no fitting or naming calls.
        import sqlite3
        source=ROOT/'data'/restore
        if not artifact.exists():shutil.copy2(source/'stream.npz',artifact)
        production.MODEL_PATH=str(artifact)
        conn=connect(OUT/f'{name}.db')
        conn.executescript(topics.SCHEMA)
        initial=json.loads((source/'stream-initial-topics.json').read_text())
        old=sqlite3.connect((source/'stream.db').resolve().as_uri()+'?mode=ro',uri=True)
        positions={i:(x,y) for i,x,y in old.execute('SELECT id,x,y FROM topics')}
        old.close()
        with conn:
            for t in initial:
                conn.execute('INSERT OR IGNORE INTO topics VALUES (?,?,?,0,0,?,?)',
                             (t['id'],t['name'],t['description'],*positions[t['id']]))
        production.setup(conn,now=end-1)
        # Bulk restoration is one transaction. Per-1000-row commits while a
        # million-row read cursor is open amplify the WAL by gigabytes.
        with conn:
            for ids,vectors in topics.iter_vectors(conn,
                    's.dead=0 AND s.deleted=0 AND e.input_version=2 '
                    'AND e.id NOT IN (SELECT id FROM story_topics) '
                    'AND e.id NOT IN (SELECT id FROM classification_queue)',size=10000):
                routing.store(conn,production.classify(conn,ids,vectors),now=end-1)
        conn.execute('UPDATE topics SET size=(SELECT count(*) FROM story_topics st WHERE st.topic=topics.id), '
                     'cohesion=(SELECT coalesce(avg(sim),0) FROM story_topics st WHERE st.topic=topics.id)')
        conn.commit();conn.close()
    elif not artifact.exists():
        topics.main(str(OUT/f'{name}.db'),str(artifact),now=end-1)
    production.MODEL_PATH=str(artifact)
    conn=connect(OUT/f'{name}.db')
    production.setup(conn,now=end-1)
    conn.execute('CREATE INDEX IF NOT EXISTS story_topics_summary ON story_topics(topic,sim)')
    conn.execute('CREATE INDEX IF NOT EXISTS embeddings_routing ON embeddings(input_version,id)')
    conn.commit()
    export_topics(conn,OUT/f'{name}-initial-topics.json',since=end-90*86400)
    save(marker,dict(topics=conn.execute('SELECT count(*) FROM topics').fetchone()[0],through=end))
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)');conn.close()


def replay():
    production.MODEL_PATH=str(OUT/'stream.npz')
    conn=connect(OUT/'stream.db')
    conn.execute('ATTACH DATABASE ? AS archive',((BASE/'corpus.db').resolve().as_uri()+'?mode=ro',))
    marker=OUT/'replay.json'
    state=json.loads(marker.read_text()) if marker.exists() else dict(through=SPLIT,weeks=0)
    while state['through']<END:
        lower=state['through'];upper=min(lower+WEEK,END);now=upper-1;started=time.monotonic()
        progress(stage='weekly_replay',week=state['weeks']+1,total_weeks=(END-SPLIT+WEEK-1)//WEEK,through=lower)
        ingest(conn,lower,upper)
        weekly.classify_pending(conn,now=now)
        events=weekly.maintain(conn,now=now)
        record=dict(week=state['weeks']+1,through=upper,seconds=time.monotonic()-started,events=events,
            topics=conn.execute("SELECT count(*) FROM topic_registry WHERE status='active'").fetchone()[0])
        with (OUT/'weeks.jsonl').open('a') as log:log.write(json.dumps(record)+'\n')
        names=[dict(zip(('id','name','description','born','status','parent'),r)) for r in conn.execute(
            'SELECT t.id,name,description,born,status,parent FROM topics t JOIN topic_registry r USING(id) ORDER BY t.id')]
        save(OUT/'latest-map.json',names)
        state=dict(through=upper,weeks=record['week'],last_week=record,complete=upper==END)
        save(marker,state);print(json.dumps(record),flush=True)
        limit=int(os.getenv('COMPARISON_WEEK_LIMIT','0'))
        if limit and state['weeks']>=limit:
            conn.close()
            progress(stage='pilot_complete',weeks=state['weeks'],through=upper)
            return
    export_topics(conn,OUT/'stream-final-topics.json',since=END-90*86400)
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)');conn.close()
    progress(stage='complete',weeks=state['weeks'],through=END)


if __name__=='__main__':
    with (OUT/'run.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        api_usage.configure(str(OUT/'api-usage.db'),15)
        save(OUT/'manifest.json',dict(MANIFEST,experiment='updated initial builds and real weekly maintenance',
             static_interval=[MANIFEST['start'],END],seed_interval=[MANIFEST['start'],SPLIT],online_interval=[SPLIT,END]))
        build('static',END)
        build('stream',SPLIT)
        replay()
