"""Reproducible read-only clustering comparison. Optional --embed makes API calls.

Cache contains only sampled public HN story vectors/IDs. Existing-input and
subject-input candidates use the same samples, seeds, and validation window.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from embed import embed_batch
from routing import metadata, subject_text
from topics import select_model


def evaluate(db, cache, output, allow_embed=False):
    c = sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        end = c.execute('SELECT max(time) FROM stories WHERE dead=0 AND deleted=0').fetchone()[0]
        if cache.exists():
            saved = np.load(cache)
            ids, subject = saved['ids'].tolist(), saved['vectors']
            rows = [c.execute('SELECT s.id,s.time,s.title,s.url,s.text,e.vec FROM stories s JOIN embeddings e USING(id) WHERE s.id=?',(i,)).fetchone() for i in ids]
        else:
            rows = c.execute('''SELECT s.id,s.time,s.title,s.url,s.text,e.vec FROM stories s JOIN embeddings e USING(id)
                WHERE s.dead=0 AND s.deleted=0 AND s.time>=? ORDER BY s.time,s.id''',(end-90*86400,)).fetchall()
            rng=np.random.default_rng(42)
            old=[r for r in rows if r[1]<end-30*86400];new=[r for r in rows if r[1]>=end-30*86400]
            rows=[old[i] for i in rng.choice(len(old),min(15000,len(old)),False)] + [new[i] for i in rng.choice(len(new),min(5000,len(new)),False)]
            if not allow_embed:
                raise ValueError('Cache missing: use --embed to authorize this bounded embedding experiment')
            texts=[subject_text(*r[2:5]) for r in rows]
            with ThreadPoolExecutor(4) as pool:
                batches=list(pool.map(embed_batch,[texts[i:i+1000] for i in range(0,len(texts),1000)]))
            subject=np.frombuffer(b''.join(v for batch in batches for v in batch),'<f4').reshape(len(rows),-1)
            cache.parent.mkdir(parents=True,exist_ok=True)
            np.savez(cache,ids=[r[0] for r in rows],vectors=subject)
        # Same canonical article cannot occur in training and validation.
        unique={}
        for i in sorted(range(len(rows)),key=lambda i:(rows[i][1],rows[i][0])):
            unique.setdefault(metadata(*rows[i][2:5])[0],i)
        indices=list(unique.values())
        before=[i for i in indices if rows[i][1]<end-30*86400]
        after=[i for i in indices if rows[i][1]>=end-30*86400]
        legacy=np.frombuffer(b''.join(r[5] for r in rows),'<f4').reshape(len(rows),-1)
        result={'as_of':end,'train_n':len(before),'validation_n':len(after),'canonical_deduplication':True,'seed':0}
        for label,vectors in [('legacy',legacy),('subject',subject)]:
            _,_,result[label]=select_model(vectors[before],vectors[after])
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
    finally:c.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default='data/hackernews.db')
    p.add_argument('--cache',type=Path,default=Path('report/pipeline-evaluation/subject-embeddings.npz'))
    p.add_argument('--output',type=Path,default=Path('report/pipeline-evaluation/comparison.json'))
    p.add_argument('--embed',action='store_true')
    args=p.parse_args();evaluate(args.db,args.cache,args.output,args.embed)
