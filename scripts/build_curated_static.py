"""Build the manually curated static map without changing its source database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from sklearn.manifold import TSNE
from core import unit
import curation
import hn_sync
import production
import routing
import topics


def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)


def prepare(plan,source,out):
    original=np.load(plan['source_model'])
    curation.validate_plan(plan,len(original['centroids']))
    centers=original['centroids'].astype('f4').copy();mean=original['mean'].astype('f4')
    for group in plan['groups']:
        if 'anchor_story_ids' in group:
            vectors=[]
            for ident in group['anchor_story_ids']:
                row=source.execute('SELECT vec FROM embeddings WHERE id=? AND input_version=2',(ident,)).fetchone()
                if not row:raise ValueError(f'Missing anchor {ident}')
                vectors.append(np.frombuffer(row[0],'<f4'))
            centers[group['prototype_ids'][0]]=unit(unit(np.stack(vectors)-mean).mean(0))
    groups=sorted(plan['groups'],key=lambda g:g['id'])
    prototype_ids=[i for g in groups for i in g['prototype_ids']]
    representative=np.stack([unit(centers[g['prototype_ids']].mean(0)) for g in groups])
    model=dict(mean=mean,centroids=representative,topic_ids=np.array([g['id'] for g in groups]),
        prototype_centroids=centers[prototype_ids],prototype_topic_ids=np.array([g['id'] for g in groups for _ in g['prototype_ids']]),
        input_version=np.array(2),min_fit=np.array(routing.MIN_FIT),min_margin=np.array(routing.MIN_MARGIN))
    np.savez(out/'topic_model.npz',**model)
    return groups,model


def main(args):
    out=args.out;out.mkdir(parents=True,exist_ok=True)
    plan=json.loads((out/'curation.json').read_text())
    signature=hashlib.sha256((out/'curation.json').read_bytes()).hexdigest()
    ready=out/'manifest.json'
    if ready.exists():
        if json.loads(ready.read_text())['curation_sha256']!=signature:raise ValueError('Use a new output for changed curation')
        print('Curated database already complete');return
    target=out/'static.db';temporary=out/'static.building.db'
    if target.exists() or temporary.exists():raise ValueError('Refuse to overwrite an existing database build')
    source=sqlite3.connect(Path(args.source).resolve().as_uri()+'?mode=ro',uri=True)
    groups,model=prepare(plan,source,out)
    if args.model_only:
        print(f'Prepared {len(groups)} curated subscriptions');return
    conn=sqlite3.connect(temporary)
    conn.execute('PRAGMA journal_mode=DELETE');conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(hn_sync.SCHEMA+topics.SCHEMA+production.SCHEMA+routing.SCHEMA)
    conn.execute('ATTACH DATABASE ? AS archive',(Path(args.source).resolve().as_uri()+'?mode=ro',))
    print('Copying public story metadata',flush=True)
    with conn:conn.execute('INSERT INTO stories SELECT * FROM archive.stories')
    end=conn.execute('SELECT max(time) FROM stories').fetchone()[0]
    xy=TSNE(2,perplexity=15,metric='cosine',init='pca',random_state=0).fit_transform(model['centroids'])
    xy=(xy-xy.min(0))/np.maximum(xy.max(0)-xy.min(0),1e-12)
    by_id={g['id']:i for i,g in enumerate(groups)}
    with conn:
        for g,pos,center in zip(groups,xy,model['centroids']):
            conn.execute('INSERT INTO topics VALUES (?,?,?,0,0,?,?)',(g['id'],g['name'],g['description'],*map(float,pos)))
            conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',(g['id'],center.astype('<f4').tobytes(),end))
        old=json.loads(Path('data/static-2020-2026/topics.json').read_text())
        for t in old:
            ident=t['id'];dest=plan['legacy_aliases'][str(ident)];center=model['centroids'][by_id[dest]]
            conn.execute('INSERT INTO topics VALUES (?,?,?,0,0,.5,.5)',(ident,t['name'],t['description']))
            conn.execute("INSERT INTO topic_registry(id,centroid,status,parent,born) VALUES (?,?,'merged',?,?)",(ident,center.astype('<f4').tobytes(),dest,end))
        conn.execute("INSERT INTO maintenance VALUES ('weekly',?)",(end,))
    processed=assigned=0
    for ids,vectors in topics.iter_vectors(source,'e.input_version=2 AND s.dead=0 AND s.deleted=0 ORDER BY e.id',size=8192):
        labels,fit,margin,accepted=curation.route(ids,vectors,model,plan.get('story_overrides',[]))
        yes=[(int(i),int(t),float(f),float(m)) for i,t,f,m,a in zip(ids,labels,fit,margin,accepted) if a]
        no=[(int(i),'low_fit' if f<routing.MIN_FIT else 'ambiguous',int(t),float(f),float(m),end) for i,t,f,m,a in zip(ids,labels,fit,margin,accepted) if not a]
        with conn:
            conn.executemany('INSERT INTO story_topics VALUES (?,?,?,?)',yes)
            conn.executemany('INSERT INTO classification_queue(id,reason,suggested_topic,sim,margin,updated_at) VALUES (?,?,?,?,?,?)',no)
        processed+=len(ids);assigned+=len(yes)
        if processed%131072<8192:print(json.dumps(dict(processed=processed,assigned=assigned)),flush=True)
    with conn:
        conn.execute('CREATE TABLE editorial_decisions(id INTEGER PRIMARY KEY,topic INTEGER,title TEXT,reason TEXT)')
        for decision in plan.get('story_overrides',[]):
            title=conn.execute('SELECT title FROM stories WHERE id=?',(decision['id'],)).fetchone()
            if not title or title[0].casefold()!=decision['title'].casefold():raise ValueError('Editorial story identity changed')
            conn.execute('INSERT INTO editorial_decisions VALUES (?,?,?,?)',(decision['id'],decision['topic'],decision['title'],decision['reason']))
        conn.execute('UPDATE topics SET size=(SELECT count(*) FROM story_topics st WHERE st.topic=topics.id),cohesion=(SELECT coalesce(avg(sim),0) FROM story_topics st WHERE st.topic=topics.id)')
        conn.execute('CREATE INDEX story_topics_summary ON story_topics(topic,sim)')
    if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database integrity failed')
    records=[dict(zip(('id','name','description','size','x','y'),r)) for r in conn.execute("SELECT t.id,name,description,size,x,y FROM topics t JOIN topic_registry r USING(id) WHERE r.status='active' ORDER BY t.id")]
    save(out/'topics.json',records)
    total=conn.execute('SELECT count(*) FROM stories').fetchone()[0]
    conn.close();os.replace(temporary,target)
    save(ready,dict(mode='manually_curated_static',curation_sha256=signature,topics=len(groups),source_topics=287,
        stories=total,classified_inputs=processed,assigned_stories=assigned,start=1577836800,through=end,
        source_database=args.source,embedding_source=args.source,
        note='Static publication database: story data and assignments only; embeddings remain in the read-only source.'))
    print(json.dumps(dict(stage='complete',topics=len(groups),assigned=assigned)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',default='data/comparison-online-v2/static.db')
    p.add_argument('--out',type=Path,default=Path('data/curated-2020-2026'))
    p.add_argument('--model-only',action='store_true')
    main(p.parse_args())
