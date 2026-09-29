"""Bounded, read-only chronological discovery diagnostic; no API calls.

Run with timeout --kill-after=5s 1080s and single-threaded BLAS.
This measures geometric coverage/persistence, NOT semantic topic accuracy.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import numpy as np
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import MiniBatchKMeans

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing import metadata, subject_text

DAY = 86400


def stamp(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())


def unit(x):
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def read_window(conn, start, end, limit, excluded=()):
    # Sample across the whole window before fetching vector blobs. No score gate.
    rows = list(conn.execute('''SELECT s.id,s.time,s.title,s.url,s.text,
        EXISTS(SELECT 1 FROM classification_queue q WHERE q.id=s.id)
        FROM stories s INDEXED BY idx_stories_time
        WHERE s.time>=? AND s.time<? AND s.type='story' AND s.dead=0 AND s.deleted=0
        AND EXISTS(SELECT 1 FROM embeddings e WHERE e.id=s.id AND e.input_version=2)
        ORDER BY s.time,s.id''', (start, end)))
    seen = set(excluded)
    unique = []
    for ident, at, title, url, body, queued in rows:
        canonical, _, domain = metadata(title, url, body)
        subject = hashlib.sha256(subject_text(title, url, body).casefold().encode()).hexdigest()
        if canonical in seen or subject in seen:
            continue
        seen.update((canonical, subject))
        unique.append(dict(id=ident, at=at, title=title, url=url, domain=domain,
                           queued=bool(queued), keys=[canonical, subject]))
    rng = np.random.default_rng(start)
    chosen = sorted(rng.choice(len(unique), min(limit, len(unique)), replace=False))
    chosen = [unique[i] for i in chosen]
    vectors = np.stack([np.frombuffer(conn.execute('SELECT vec FROM embeddings WHERE id=?',
        (r['id'],)).fetchone()[0], '<f4') for r in chosen])
    return chosen, unit(vectors), dict(embedded_live=len(rows), unique=len(unique), sampled=len(chosen)), seen


def fit(x, k, seed):
    model = MiniBatchKMeans(n_clusters=k, batch_size=1024, n_init=3,
                           max_iter=80, random_state=seed).fit(x)
    return unit(model.cluster_centers_)


def evaluate(train_rows, train, test_rows, test, centers, baseline, cutoff):
    sims = train @ centers.T
    labels = sims.argmax(1)
    future_sims = test @ centers.T
    future_labels = future_sims.argmax(1)
    candidates = []
    for label, center in enumerate(centers):
        ix = np.flatnonzero(labels == label)
        if len(ix) < 20:
            continue
        domains = [train_rows[i]['domain'] for i in ix if train_rows[i]['domain']]
        sites = set(domains)
        weeks = {(train_rows[i]['at'] - (cutoff-28*DAY)) // (7*DAY) for i in ix}
        if len(sites) < 5 or len(weeks) < 3:
            continue
        ranked = ix[np.argsort(-sims[ix, label])]
        radius = float(np.quantile(sims[ix, label], .1))
        future = np.flatnonzero((future_labels == label) & (future_sims[:, label] >= radius))
        novelty = float(1 - (baseline @ center).max())
        candidates.append(dict(cell=int(label),posts=len(ix),sites=len(sites),weeks=len(weeks),
            max_source_share=max((domains.count(d)/len(ix) for d in sites), default=0),
            novelty=novelty, train_cohesion=float(sims[ix,label].mean()), radius=radius,
            future_posts=len(future), persistent=len(future)>=5,
            examples=[dict(id=train_rows[i]['id'],title=train_rows[i]['title'])
                      for i in list(ranked[:3])+list(ranked[-3:])],
            future_examples=[dict(id=test_rows[i]['id'],title=test_rows[i]['title']) for i in future[:5]]))
    # Selection uses only training data, never future persistence.
    candidates.sort(key=lambda c: (-c['novelty'], -c['posts']))
    return candidates[:10]


def run(db, output):
    started = time.monotonic()
    conn = sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro', uri=True)
    conn.execute('PRAGMA cache_size=-65536')
    conn.execute('BEGIN')
    output.mkdir(parents=True, exist_ok=True)
    report = dict(protocol='12 chronological folds; frozen vs rolling 80 cells, two seeds; no labels, APIs, production writes',
                  caveats=['Current story bodies/deletions and stored embeddings are retrospective, not historical snapshots.',
                           'Vector fit and persistence are not semantic precision, novelty, or subscription usefulness.',
                           'Queue counts are current snapshot diagnostics, not historical replay.'],
                  config=dict(k=80,train_days=28,test_days=14,train_cap=6000,test_cap=3000,seeds=[42,43]),
                  folds=[])
    base_rows, base_x, counts, base_keys = read_window(conn, stamp('2025-08-04'), stamp('2025-09-01'),6000)
    baselines = {seed:fit(base_x,80,seed) for seed in (42,43)}
    report['baseline'] = dict(start='2025-08-04',end='2025-09-01',counts=counts)
    fingerprint=hashlib.sha256()
    for row,x in zip(base_rows,base_x):
        fingerprint.update(str(row['id']).encode());fingerprint.update(x.tobytes())
    for year,month in [(2025,m) for m in range(10,13)]+[(2026,m) for m in range(1,10)]:
        if time.monotonic()-started>1000:
            raise TimeoutError('Internal experiment deadline')
        date=f'{year}-{month:02d}-01'; cutoff=stamp(date)
        train_rows,x,train_counts,train_keys=read_window(conn,cutoff-28*DAY,cutoff,6000)
        test_rows,y,test_counts,_=read_window(conn,cutoff,cutoff+14*DAY,3000,base_keys|train_keys)
        for rows,vectors in [(train_rows,x),(test_rows,y)]:
            for row,vector in zip(rows,vectors):
                fingerprint.update(str(row['id']).encode());fingerprint.update(vector.tobytes())
        fold=dict(cutoff=date,train=train_counts,test=test_counts,
                  queued_in_train_sample=sum(r['queued'] for r in train_rows),seeds=[])
        fitted=[]
        for seed in (42,43):
            centers=fit(x,80,seed);fitted.append(centers)
            static_loss=1-(y@baselines[seed].T).max(1)
            rolling_loss=1-(y@centers.T).max(1)
            candidates=evaluate(train_rows,x,test_rows,y,centers,baselines[seed],cutoff)
            fold['seeds'].append(dict(seed=seed,static_loss=float(static_loss.mean()),
                rolling_loss=float(rolling_loss.mean()),delta=float((rolling_loss-static_loss).mean()),
                candidates=candidates,persistent=sum(c['persistent'] for c in candidates)))
        sim=fitted[0]@fitted[1].T
        a,b=linear_sum_assignment(-sim)
        fold['seed_matched_center_similarity']=float(sim[a,b].mean())
        report['folds'].append(fold)
        report['seconds']=time.monotonic()-started
        (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
        print(date,'seconds',round(report['seconds'],1),'delta',round(fold['seeds'][0]['delta'],5),
              'persistent',fold['seeds'][0]['persistent'],flush=True)
    # Observe current operational health separately from the temporal comparison.
    latest=conn.execute('SELECT max(time) FROM stories WHERE time<=?',(int(time.time()),)).fetchone()[0]
    report['current_health']=dict(zip(['embedded','queued','assigned','eligible_training'],conn.execute('''
        SELECT count(*),sum(EXISTS(SELECT 1 FROM classification_queue q WHERE q.id=s.id)),
        sum(EXISTS(SELECT 1 FROM story_topics st WHERE st.id=s.id)),
        sum(EXISTS(SELECT 1 FROM story_topics st WHERE st.id=s.id) AND NOT EXISTS
            (SELECT 1 FROM story_topic_labels l WHERE l.id=s.id))
        FROM stories s JOIN embeddings e USING(id) WHERE s.time>=? AND s.time<=?
        AND e.input_version=2 AND s.dead=0 AND s.deleted=0''',(latest-365*DAY,latest)).fetchone()))
    report['input_sha256']=fingerprint.hexdigest()
    report['seconds']=time.monotonic()-started
    report['api_calls']=0
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    conn.close()
    print('Finished',round(report['seconds'],1),'seconds',report['current_health'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default='data/atlas.db');p.add_argument('--output',type=Path,default=Path('report/topic-refresh'))
    args=p.parse_args();run(args.db,args.output)
