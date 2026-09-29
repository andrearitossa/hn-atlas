"""Read-only real-data checks. Run with `timeout --kill-after=5s 1080s ...`.

Default: topic activity, backlog and missing-topic recovery, no API calls.
--llm: additionally audit sampled public posts and simulate repairs in memory.
Never modifies the source database or sends email.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hn_sync
import map_health
import refresh
import routing
import topics
from config import DB
from database import connect


def check(source, output, use_llm=False):
    started=time.monotonic();now=int(time.time())
    output.mkdir(parents=True,exist_ok=True)
    with connect(source,readonly=True) as conn:
        conn.execute('BEGIN')  # Every comparison uses the same snapshot.
        packets=map_health.samples(conn,now)
        coverage=dict(zip(('live','embedded','assigned','queued','pending'),conn.execute('''
            SELECT count(*),sum(e.input_version=2),sum(st.id IS NOT NULL),sum(q.id IS NOT NULL),
            sum(e.input_version=2 AND st.id IS NULL AND q.id IS NULL)
            FROM stories s LEFT JOIN embeddings e USING(id) LEFT JOIN story_topics st USING(id)
            LEFT JOIN classification_queue q USING(id) WHERE s.time>? AND s.time<=? AND s.dead=0 AND s.deleted=0''',
            (now-60*refresh.DAY,now)).fetchone()))
        report=dict(as_of=now,coverage=coverage,topics=packets)
        if use_llm:
            batches=[packets[i:i+8] for i in range(0,len(packets),8)]
            answers=[]
            with ThreadPoolExecutor(4) as pool:
                for batch,answer in zip(batches,pool.map(map_health.review,batches)):
                    answers.extend(map_health.validate(answer,batch))
                    print(f'Reviewed {len(answers)}/{len(packets)} topics',flush=True)
            report['reviews']=answers
            # Save paid results before the independent assignment check.
            (output/'review.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
            sandbox=sqlite3.connect(':memory:')
            sandbox.executescript(hn_sync.SCHEMA+topics.TOPIC_SCHEMA+topics.SCHEMA+routing.SCHEMA+
                refresh.SCHEMA+map_health.SCHEMA+'''CREATE TABLE embeddings(id INTEGER PRIMARY KEY,vec BLOB,input_version INTEGER);
                CREATE TABLE editorial_decisions(id INTEGER PRIMARY KEY,topic INTEGER);''')
            for table in ('topics','topic_registry','topic_centers'):
                for row in conn.execute('SELECT * FROM '+table):
                    sandbox.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in row)+')',tuple(row))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='editorial_decisions'").fetchone():
                sandbox.executemany('INSERT INTO editorial_decisions VALUES (?,?)',conn.execute('SELECT id,topic FROM editorial_decisions'))
            for packet in packets:
                for item in packet['stories']:
                    ident=item['id'];row=conn.execute('SELECT * FROM stories WHERE id=?',(ident,)).fetchone()
                    sandbox.execute('INSERT INTO stories VALUES ('+','.join('?' for _ in row)+')',tuple(row))
                    sandbox.execute('INSERT INTO story_topics VALUES (?,?,?,?)',tuple(conn.execute('SELECT * FROM story_topics WHERE id=?',(ident,)).fetchone()))
                    row=conn.execute('SELECT id,vec,input_version FROM embeddings WHERE id=?',(ident,)).fetchone()
                    if row:sandbox.execute('INSERT INTO embeddings VALUES (?,?,?)',tuple(row))
            cached={a['id']:a for a in answers}
            report['repair_summary']=map_health.audit(sandbox,now,inspect=lambda batch:[cached[p['id']] for p in batch])
            names={p['id']:p['name'] for p in packets};changes=[]
            for packet in packets:
                for item in packet['stories']:
                    row=sandbox.execute('SELECT topic FROM story_topics WHERE id=?',(item['id'],)).fetchone()
                    target=row[0] if row else None
                    if target!=packet['id']:
                        changes.append(dict(id=item['id'],title=item['title'],before=packet['name'],after=names.get(target)))
            report['simulated_repairs']=changes
            sandbox.close()
        else:
            model=refresh.load_model(conn);recovery=[]
            for name in ('Apple ecosystem','Rust programming','Robotics'):
                ident=next(p['id'] for p in packets if p['name']==name)
                conn.execute('DROP TABLE IF EXISTS temp.classification_queue')
                conn.execute('CREATE TEMP TABLE classification_queue AS SELECT id FROM main.classification_queue UNION SELECT id FROM story_topics WHERE topic=?',(ident,))
                mask=model['prototype_topic_ids']!=ident
                hidden=dict(model,prototype_topic_ids=model['prototype_topic_ids'][mask],prototype_centroids=model['prototype_centroids'][mask],curated=int(mask[:model['curated']].sum()))
                truth={r[0] for r in conn.execute('SELECT s.title FROM stories s JOIN story_topics st USING(id) WHERE st.topic=?',(ident,))}
                groups=refresh.candidates(conn,now,hidden)
                scores=[dict(posts=g['posts'],sample_match=sum(s['title'] in truth for s in g['recent_sample'])/len(g['recent_sample']),titles=g['titles']) for g in groups]
                scores.sort(key=lambda g:(g['sample_match'],g['posts']),reverse=True)
                recovery.append(dict(topic=name,best=scores[:3]))
                print('Recovery',name,scores[0]['sample_match'] if scores else 0,flush=True)
            report['recovery']=recovery
        report['seconds']=time.monotonic()-started
        (output/('llm.json' if use_llm else 'offline.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False))
        print(json.dumps(dict(seconds=report['seconds'],coverage=coverage,repair=report.get('repair_summary'))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DB)
    parser.add_argument('--output',type=Path,default=Path('report/topic-maintenance'))
    parser.add_argument('--llm',action='store_true')
    args=parser.parse_args()
    check(args.db,args.output,args.llm)
