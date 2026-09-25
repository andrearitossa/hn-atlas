"""Real-data regression snapshots; reads baseline, writes only report artifacts."""
import json
import sqlite3
import time
from pathlib import Path
import numpy as np
import embed
import hn_sync
import production
import routing
import structure
import topics
from core import unit

OUT=Path('report/pipeline-simplification'); OUT.mkdir(exist_ok=True,parents=True)
BASE=Path('data/comparison-2020')

def run(label, now, seed):
    start=time.monotonic()
    db=sqlite3.connect(':memory:')
    db.executescript(hn_sync.SCHEMA+topics.SCHEMA+production.SCHEMA+routing.SCHEMA)
    db.execute('CREATE TABLE embeddings(id INTEGER PRIMARY KEY,vec BLOB,input_version INTEGER)')
    db.execute('ATTACH DATABASE ? AS source',((BASE/('corpus.db' if seed else 'stream.db')).resolve().as_uri()+'?mode=ro',))
    db.execute('INSERT INTO stories SELECT * FROM source.stories WHERE time>=? AND time<=? AND dead=0 AND deleted=0',(now-28*86400,now))
    db.execute('INSERT INTO embeddings SELECT e.id,e.vec,e.input_version FROM source.embeddings e JOIN stories s USING(id) WHERE e.input_version=2')
    production.MODEL_PATH=str(BASE/'stream.npz')
    if seed:
        initial=json.loads((BASE/'maps/stream-initial.json').read_text())['nodes'];model=np.load(production.MODEL_PATH)
        for n in initial:
            db.execute('INSERT INTO topics VALUES (?,?,?,?,?,?,?)',(n['id'],n['name'],n['description'],0,0,n['x'],n['y']))
            db.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',(n['id'],model['centroids'][n['id']].astype('<f4').tobytes(),1704067199))
        for ids,vec in topics.iter_vectors(db):
            routing.store(db,production.classify(db,ids,vec),now)
    else:
        db.execute('INSERT INTO topics SELECT * FROM source.topics')
        db.execute('INSERT INTO topic_registry(id,centroid,status,parent,born,reviewed_at) SELECT id,centroid,status,parent,born,reviewed_at FROM source.topic_registry')
        db.execute('INSERT INTO story_topics SELECT st.* FROM source.story_topics st JOIN stories s USING(id)')
    # Add real historical exemplars, not vectors or future data, to preserve ID meaning.
    db.execute('ATTACH DATABASE ? AS history',((BASE/'stream.db').resolve().as_uri()+'?mode=ro',))
    for (topic,) in db.execute('SELECT id FROM topics').fetchall():
        history_ids=[r[0] for r in db.execute("""SELECT st.id FROM history.story_topics st JOIN history.stories s USING(id)
            WHERE st.topic=? AND s.time<? AND s.dead=0 AND s.deleted=0 ORDER BY st.id LIMIT 8""",
            (topic,min(now-28*86400,1704067200) if seed else now-28*86400))]
        for ident in history_ids:
            db.execute('INSERT OR IGNORE INTO stories SELECT * FROM history.stories WHERE id=?',(ident,))
            db.execute('INSERT OR IGNORE INTO story_topics SELECT * FROM history.story_topics WHERE id=?',(ident,))
    db.commit()
    rows,x=production.recent_vectors(db,now-28*86400,now)
    packet,groups=structure.evidence(db,rows,x)
    (OUT/f'{label}-evidence.json').write_text(json.dumps(packet,indent=2))
    print(label,len(rows),'stories',len(groups),'groups;',
          'reusing saved review' if '--cached' in __import__('sys').argv else 'requesting map review',flush=True)
    result=(json.loads((OUT/f'{label}-plan.json').read_text()) if '--cached' in __import__('sys').argv else
            structure.ask_json(structure.PROMPT+json.dumps(packet),model=routing.REVIEW_MODEL,reasoning_effort='medium'))
    (OUT/f'{label}-plan.json').write_text(json.dumps(result,indent=2))
    changes,rejected=structure.accepted_changes(result,packet)
    with db:
        events=structure.apply(db,changes,groups,rows,x,now)
    after=[]
    for ident,name,description in db.execute("SELECT t.id,name,description FROM topics t JOIN topic_registry r USING(id) WHERE r.status='active'"):
        own={r[0] for r in db.execute('SELECT id FROM story_topics WHERE topic=?',(ident,))}
        part=np.array([i for i,r in enumerate(rows) if r[0] in own],dtype=int)
        after.append(dict(id=ident,name=name,description=description,count=len(part),titles=structure.sample(rows,x,part)))
    (OUT/f'{label}-after.json').write_text(json.dumps(after,indent=2))
    if seed:
        import re
        chosen=next((s for c in changes for s in c['subjects'] if s['name']=='Model Context Protocol'),None)
        if chosen:
            topic=db.execute("SELECT id FROM topics WHERE name='Model Context Protocol'").fetchone()[0]
            _,ids,centers=production.model(db);pos=list(ids).index(topic)
            original=unit(x[np.concatenate([groups[g] for g in chosen['groups']])].mean(0))
            variants={}
            for method,center,floor in [('whole_cluster',original,routing.MIN_FIT),
                    ('selected_story_anchors',centers[pos],routing.MIN_FIT),
                    ('anchors_and_admission_floor',centers[pos],routing.BOUNDARY_FIT)]:
                trial=centers.copy();trial[pos]=center;scores=x@trial.T
                top=scores.argmax(1);fit=scores[np.arange(len(rows)),top]
                scores[np.arange(len(rows)),top]=-1;margin=fit-scores.max(1)
                members=[dict(id=int(r[0]),title=r[2],fit=float(f),margin=float(m))
                    for r,t,f,m in zip(rows,top,fit,margin)
                    if t==pos and f>=floor and m>=routing.MIN_MARGIN]
                explicit=[r for r in members if re.search(r'\bMCP\b|model context protocol',r['title'],re.I)]
                variants[method]=dict(stories=len(members),explicit_mcp_titles=len(explicit),members=members)
            (OUT/'mcp-routing-diagnostic.json').write_text(json.dumps(dict(
                caveat='Explicit-title proxy, not human-labeled semantic accuracy; same other topic centers in both variants.',
                variants=variants),indent=2))
    summary=dict(stories=len(rows),groups=len(groups),events=events,rejected=rejected,seconds=time.monotonic()-start)
    (OUT/f'{label}-result.json').write_text(json.dumps(summary,indent=2))
    print(label,json.dumps(summary),flush=True)
    db.close()

if __name__=='__main__':
    import sys
    label=sys.argv[1]
    run(label,1734825599 if label=='mcp-2024' else 1790326180,label=='mcp-2024')
