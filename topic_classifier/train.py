"""Fit and atomically store the production classifier: python -m topic_classifier.train."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp
from sklearn.mixture import GaussianMixture
import sklearn

from config import DB
from routing import subject_text, metadata
from topic_classifier import Classifier, FORMAT_VERSION, MODEL_PATH

DAY = 86400


def train(db, output):
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
    conn.execute('BEGIN')
    directory = list(conn.execute('SELECT t.id,t.name,t.description FROM topics t JOIN topic_registry r USING(id) ORDER BY t.id'))
    active = {r[0] for r in directory}
    now = int(time.time())
    end = conn.execute('SELECT max(s.time)+1 FROM embeddings e JOIN stories s USING(id) WHERE e.input_version=2 AND s.time<=?', (now,)).fetchone()[0]
    cutoff, start = end - 14*DAY, end - 365*DAY
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    edits = dict(conn.execute('SELECT id,topic FROM editorial_decisions')) if 'editorial_decisions' in tables else {}
    not_self = 'AND NOT EXISTS (SELECT 1 FROM story_topic_labels l WHERE l.id=s.id)' if 'story_topic_labels' in tables else ''
    rows = conn.execute(f'''SELECT s.id,s.time,s.title,s.url,s.text,e.vec,st.topic
        FROM stories s JOIN embeddings e USING(id) JOIN story_topics st USING(id)
        WHERE s.dead=0 AND s.deleted=0 AND e.input_version=2 AND s.time>=? AND s.time<?
        AND (s.time>=? OR s.id%3=0) {not_self} ORDER BY s.time,s.id''', (start,end,cutoff))
    data = {'train': [], 'calibration': []}; seen=set(); ids_hash=hashlib.sha256()
    for ident, at, title, url, body, vector, topic in rows:
        topic = edits.get(ident, topic)
        if topic not in active:
            continue
        text=subject_text(title,url,body)
        keys=(metadata(title,url,body)[0],hashlib.sha256(text.casefold().encode()).hexdigest())
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        data['calibration' if at>=cutoff else 'train'].append((ident,vector,topic))
        ids_hash.update(f'{ident}:{topic}:'.encode());ids_hash.update(vector)
    conn.close()
    x={k:np.stack([np.frombuffer(r[1],'<f4') for r in rows]).astype(np.float64) for k,rows in data.items()}
    y={k:np.array([r[2] for r in rows]) for k,rows in data.items()}
    classes,counts=np.unique(y['train'],return_counts=True)
    if set(classes)!=active or counts.min()<2 or x['train'].shape[1]!=512:
        raise ValueError('Each active topic needs at least two labeled training stories with 512-dimensional embeddings')
    means=[];variances=[];weights=[];fitted=[]
    for label in classes:
        model=GaussianMixture(n_components=2,covariance_type='diag',reg_covar=1e-6,n_init=1,max_iter=200,random_state=42)
        model.fit(x['train'][y['train']==label])
        if not model.converged_:
            raise ValueError(f'Mixture did not converge for topic {label}')
        means.append(model.means_);variances.append(model.covariances_);weights.append(model.weights_);fitted.append(model)
    priors=counts/counts.sum()
    scores=np.column_stack([m.score_samples(x['calibration']) for m in fitted])+np.log(priors)
    target=np.searchsorted(classes,y['calibration'])
    def loss(log_t):
        s=scores/np.exp(log_t)
        return float(np.mean(logsumexp(s,axis=1)-s[np.arange(len(s)),target]))
    fit=minimize_scalar(loss,bounds=(-3,6),method='bounded')
    if not fit.success or not np.isfinite(fit.fun) or fit.fun>loss(0):
        raise ValueError('Temperature calibration failed')
    description=dict(format_version=FORMAT_VERSION,model_id='',trained_at=datetime.now(timezone.utc).isoformat(),
        embedding_model='text-embedding-3-small',embedding_dimensions=512,input_version=2,
        components_per_topic=2,covariance_type='diag',reg_covar=1e-6,seed=42,sklearn_version=sklearn.__version__,
        threshold=.7,max_labels=3,temperature=float(np.exp(fit.x)),topic_count=len(classes),
        train_start=start,calibration_start=cutoff,end_exclusive=end,
        counts={key:len(value) for key,value in data.items()},training_counts={str(i):int(n) for i,n in zip(classes,counts)},
        sample='Training story IDs divisible by 3; all calibration stories; canonical/subject deduplication; no queue labels; no prior GMM outputs.',
        training_sha256=ids_hash.hexdigest(),taxonomy_sha256=hashlib.sha256(json.dumps(directory).encode()).hexdigest(),
        calibration_logloss_raw=loss(0),calibration_logloss_scaled=float(fit.fun),
        calibration_top1_agreement=float(np.mean(classes[scores.argmax(axis=1)]==y['calibration'])),
        label_warning='Historical assignments are weak labels. Temperature does not validate secondary-topic relevance.')
    arrays=dict(classes=classes,means=np.array(means),variances=np.array(variances),weights=np.array(weights),priors=priors)
    fingerprint=hashlib.sha256()
    for key in sorted(arrays):fingerprint.update(arrays[key].tobytes())
    fingerprint.update(json.dumps(description,sort_keys=True).encode())
    description['model_id']='gmm2-'+fingerprint.hexdigest()[:16]
    arrays['metadata']=np.array(json.dumps(description,sort_keys=True))
    check=Classifier(arrays)
    np.testing.assert_allclose(check.probabilities(x['calibration'][:64]),
        np.exp(scores[:64]/check.temperature-logsumexp(scores[:64]/check.temperature,axis=1)[:,None]),rtol=1e-6,atol=1e-7)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent,suffix='.npz',delete=False) as file:
        temporary=Path(file.name)
        np.savez_compressed(file,**arrays)
    try:
        os.chmod(temporary, 0o644)
        os.replace(temporary,output)
    finally:
        temporary.unlink(missing_ok=True)
    description['artifact_sha256']=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.json').write_text(json.dumps(description,indent=2)+'\n')
    print(json.dumps(description,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default=str(DB));parser.add_argument('--output',default=str(MODEL_PATH))
    args=parser.parse_args();train(args.db,args.output)
