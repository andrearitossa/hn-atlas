"""Read-only coverage, overlap, discovery and evidence for the historical comparison."""
from collections import Counter
import argparse
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
from sklearn.metrics import adjusted_rand_score,normalized_mutual_info_score

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import production
import routing
from scripts.compare_history import export_topics,save,SPLIT,START


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)


def stats(conn,end):
    result={}
    for name,since in [('all',START),('since_2024',SPLIT),('last_90_days',end-90*86400)]:
        total,assigned=conn.execute('''SELECT count(*),count(st.id) FROM stories s
            LEFT JOIN story_topics st USING(id) WHERE s.time>=? AND s.time<?
            AND s.dead=0 AND s.deleted=0''',(since,end)).fetchone()
        result[name]=dict(stories=total,assigned=assigned,coverage=assigned/total if total else 0)
        popular,covered=conn.execute('''SELECT count(*),count(st.id) FROM stories s
            LEFT JOIN story_topics st USING(id) WHERE s.time>=? AND s.time<?
            AND s.dead=0 AND s.deleted=0 AND (s.score>=100 OR s.descendants>=50)''',(since,end)).fetchone()
        result[name]['high_interest']=dict(stories=popular,assigned=covered,coverage=covered/popular if popular else 0)
    result['queue']=dict(conn.execute('SELECT reason,count(*) FROM classification_queue GROUP BY reason'))
    result['active_topics']=conn.execute("SELECT count(*) FROM topic_registry WHERE status='active'").fetchone()[0]
    result['aliases']=conn.execute("SELECT count(*) FROM topic_registry WHERE status='merged'").fetchone()[0]
    result['reviewed_articles']=conn.execute('SELECT count(*) FROM article_decisions').fetchone()[0]
    result['old_pending']=conn.execute('''SELECT count(*) FROM classification_queue q JOIN stories s USING(id)
        WHERE s.time<?''',(end-28*86400,)).fetchone()[0]
    result['candidates']=dict(conn.execute('SELECT status,count(*) FROM topic_candidates GROUP BY status'))
    return result


def model(conn,path):
    production.MODEL_PATH=str(path.resolve())
    mean,ids,centers=production.model(conn)
    return mean,ids,centers


def predictions(vectors,artifact):
    mean,ids,centers=artifact
    top,fit,margin=routing.decisions(vectors,mean,centers)
    return ids[top],fit,margin


def measure(out):
    manifest=json.loads((out/'manifest.json').read_text())
    progress=json.loads((out/'stream-progress.json').read_text())
    if not progress.get('complete') or progress['through']!=manifest['end']:
        raise ValueError('Full streaming replay must finish before comparison')
    static=readonly(out/'static.db');stream=readonly(out/'stream.db')
    source=readonly(out/'corpus.db')
    try:
        source.execute('ATTACH DATABASE ? AS fixed',((out/'static.db').resolve().as_uri()+'?mode=ro',))
        source.execute('ATTACH DATABASE ? AS replay',((out/'stream.db').resolve().as_uri()+'?mode=ro',))
        models=[model(static,out/'static.npz'),model(stream,out/'stream.npz')]
        names=[dict(c.execute('SELECT id,name FROM topics')) for c in (static,stream)]
        data={};sample_count=0;labels=[[],[]];published=[[],[]];pair_counts=Counter(); examples={}
        # Approximately 51k post-2024 stories, deterministic and spread through time.
        cursor=source.execute('''SELECT s.id,s.title,s.time,e.vec,a.topic,b.topic FROM stories s
            JOIN embeddings e USING(id) LEFT JOIN fixed.story_topics a ON a.id=s.id
            LEFT JOIN replay.story_topics b ON b.id=s.id
            WHERE s.time>=? AND s.id%17=0 ORDER BY s.id''',(SPLIT,))
        while rows:=cursor.fetchmany(2000):
            vectors=np.frombuffer(b''.join(r[3] for r in rows),'<f4').reshape(len(rows),-1)
            pred=[predictions(vectors,m) for m in models]
            for j in range(2):
                labels[j].extend(pred[j][0].tolist())
                published[j].extend(r[4+j] if r[4+j] is not None else -1 for r in rows)
            for i,row in enumerate(rows):
                a,b=int(pred[0][0][i]),int(pred[1][0][i])
                pair_counts[(a,b)]+=1
                if a not in examples:
                    examples[a]=[]
                if len(examples[a])<10:
                    examples[a].append(dict(id=row[0],title=row[1],static_topic=names[0][a],
                        stream_topic=names[1][b],static_published=row[4],stream_published=row[5]))
            sample_count+=len(rows)
        support=Counter(labels[0]);stream_support=Counter(labels[1])
        matches=[]
        for topic,count in support.most_common():
            best=sorted([(v,b) for (a,b),v in pair_counts.items() if a==topic],reverse=True)[:3]
            matches.append(dict(static_id=topic,static_name=names[0][topic],sample_support=count,
                candidates=[dict(stream_id=b,stream_name=names[1][b],intersection=v,
                    static_fraction=v/count,jaccard=v/(count+stream_support[b]-v)) for v,b in best],
                examples=examples[topic]))
        weeks=[json.loads(line) for line in (out/'weeks.jsonl').read_text().splitlines()]
        events=[dict(date=w['date'],event=event) for w in weeks for event in w['events']]
        usage=readonly(out/'api-usage.db')
        costs=[dict(model=r[0],requests=r[1],ledger_usd=r[2],completed=r[3]) for r in usage.execute(
            "SELECT model,count(*),sum(cost),sum(status='complete') FROM calls GROUP BY model")]
        usage.close()
        data=dict(manifest=manifest,static=stats(static,manifest['end']),stream=stats(stream,manifest['end']),
            initial_stream_topics=len(json.loads((out/'stream-initial-topics.json').read_text())),
            replay_weeks=len(weeks),events=events,api_usage=costs,
            raw_classifier_comparison=dict(sample_size=sample_count,
                adjusted_rand=adjusted_rand_score(*labels),normalized_mutual_information=normalized_mutual_info_score(*labels),
                note='Partition agreement, not accuracy; topic IDs and training means differ.'),
            overlap=matches)
        fixtures=json.loads(Path('tests/fixtures/topic_routing.json').read_text())
        data['targeted_cases']=[]
        for case in fixtures:
            row=dict(id=case['id'],title=case['title'])
            for label,conn in [('static',static),('stream',stream)]:
                assigned=conn.execute('''SELECT t.name,t.description,st.sim,st.margin FROM story_topics st
                    JOIN topics t ON t.id=st.topic WHERE st.id=?''',(case['id'],)).fetchone()
                queued=conn.execute('''SELECT q.reason,t.name,q.sim,q.margin FROM classification_queue q
                    LEFT JOIN topics t ON t.id=q.suggested_topic WHERE q.id=?''',(case['id'],)).fetchone()
                row[label]=dict(assigned=assigned,queued=queued)
            data['targeted_cases'].append(row)
        save(out/'metrics.json',data)
        export_topics(static,out/'static-final-topics.json',since=manifest['end']-90*86400)
        export_topics(stream,out/'stream-final-topics.json',since=manifest['end']-90*86400)
        print(json.dumps({k:v for k,v in data.items() if k not in ('overlap','events')},indent=2))
        return data
    finally:
        source.close();static.close();stream.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('data/comparison-2020'))
    measure(parser.parse_args().out)
