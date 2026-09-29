import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.special import logsumexp, softmax
from scipy.stats import norm

import attention
import catalog
import hn_sync
import newsletter
import refresh
import routing
import timeline
import topic_classifier
import topics


def classifier(priors, temperature=1.):
    n=len(priors)
    return topic_classifier.Classifier(dict(classes=np.arange(1000,1000+n),
        means=np.zeros((n,2,512)),variances=np.ones((n,2,512)),weights=np.full((n,2),.5),
        priors=np.array(priors),metadata=np.array(json.dumps(dict(
            format_version=1,temperature=temperature,model_id='unit-model')))))


class ModelTests(unittest.TestCase):
    def test_cumulative_rule_and_three_topic_cap(self):
        for priors, expected, reached in [([.8,.2],[1000],True),([.5,.3,.2],[1000,1001],True),
                ([.4,.25,.2,.15],[1000,1001,1002],True),([1/6]*6,[1000,1001,1002],False)]:
            labels,p,ok=classifier(priors).predict(np.zeros((1,512)))[0]
            self.assertEqual(labels,expected);self.assertEqual(ok,reached)
            np.testing.assert_allclose(p,priors[:len(labels)])

    def test_numpy_inference_matches_direct_gaussian_densities(self):
        rng=np.random.default_rng(7);m=classifier([.4,.35,.25],temperature=3)
        means=rng.normal(0,.1,(3,2,512));variances=rng.uniform(.2,2,(3,2,512))
        data=dict(classes=m.classes,means=means,variances=variances,weights=m.weights,priors=m.priors,
                  metadata=np.array(json.dumps(m.metadata)))
        model=topic_classifier.Classifier(data);x=rng.normal(size=(4,512))
        scores=np.column_stack([logsumexp(norm.logpdf(x[:,None,:],means[i],np.sqrt(variances[i])).sum(2)
                                          +np.log(m.weights[i]),axis=1) for i in range(3)])+np.log(m.priors)
        np.testing.assert_allclose(model.probabilities(x),softmax(scores/3,axis=1),rtol=1e-10,atol=1e-10)
        self.assertEqual(model.predict(np.empty((0,512))),[])
        with self.assertRaises(ValueError):model.predict(np.zeros((1,8)))
        with self.assertRaises(ValueError):model.predict(np.full((1,512),np.nan))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row;self.addCleanup(self.conn.close)
        self.conn.executescript(hn_sync.SCHEMA+topics.TOPIC_SCHEMA+topics.SCHEMA+routing.SCHEMA+refresh.SCHEMA+
            'CREATE TABLE embeddings(id INTEGER PRIMARY KEY,vec BLOB,input_version INTEGER);')
        self.model=classifier([.4,.35,.25]);centers=np.eye(3,512,dtype='f4')
        self.path=Path(self.tmp.name)/'prototypes.npz'
        np.savez(self.path,mean=np.zeros(512),prototype_topic_ids=self.model.classes,
                 prototype_centroids=centers,centroids=centers,topic_ids=self.model.classes)
        for ident,center in zip(self.model.classes.tolist(),centers):
            self.conn.execute('INSERT INTO topics VALUES (?,?,?,0,0,.5,.5)',(ident,f'Topic {ident}',f'Subject {ident}'))
            self.conn.execute('INSERT INTO topic_registry VALUES (?,?,0,0)',(ident,center.tobytes()))
        self.now=1790000000
        for ident in (1,2):
            self.conn.execute('INSERT INTO stories(id,type,title,url,time,score,descendants,fetched_at) VALUES (?,?,?,?,?,?,0,?)',
                              (ident,'story',f'Story {ident}',f'https://example.com/{ident}',self.now-100,30,self.now))
            self.conn.execute('INSERT INTO embeddings VALUES (?,?,2)',(ident,np.zeros(512,'f4').tobytes()))
        self.conn.commit()
        for target,attr,value in [(topic_classifier,'load',lambda:self.model),(topics,'MODEL_PATH',self.path),
                                  (refresh,'MODEL_PATH',self.path)]:
            p=patch.object(target,attr,value);p.start();self.addCleanup(p.stop)

    def test_daily_files_multiple_topics_and_is_idempotent_without_llm(self):
        with patch.object(routing,'semantic_decisions',side_effect=AssertionError('Unexpected API call')):
            report=refresh.refresh(self.conn,self.now,fetch=lambda c,n:None,embed=lambda c,n:None,discover_topics=False)
            self.assertEqual(report['filed'],2)
            self.assertEqual(refresh.classify(self.conn,self.now),0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topic_labels').fetchone()[0],4)
        for topic in (1000,1001):
            self.assertEqual(len(attention.posts(self.conn,topic,self.now)),2)
            self.assertEqual(catalog.detail(self.conn,topic,self.now)['size'],2)
            self.assertEqual(len(newsletter.candidates(self.conn,topic,self.now-200,self.now)),2)
            self.assertEqual(sum(p['count'] for p in timeline.build(self.conn,topic,30,self.now)['chapters']),2)
        self.assertEqual(sum(p['count'] for p in timeline.build(self.conn,None,30,self.now)['chapters']),2)

    def test_editorial_override_and_existing_queue_remain_authoritative(self):
        self.conn.execute('CREATE TABLE editorial_decisions(id INTEGER PRIMARY KEY,topic INTEGER)')
        self.conn.execute('INSERT INTO editorial_decisions VALUES (1,1002)')
        self.conn.execute("INSERT INTO classification_queue(id,reason,updated_at) VALUES (2,'low_fit',0)")
        self.assertEqual(refresh.classify(self.conn,self.now),1)
        rows=self.conn.execute('SELECT topic,rank,model_version FROM story_topic_labels').fetchall()
        self.assertEqual([tuple(r) for r in rows],[(1002,1,'editorial')])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM classification_queue').fetchone()[0],1)

    def test_content_edit_and_manual_replacement_remove_secondary_labels(self):
        refresh.classify(self.conn,self.now)
        hn_sync.save_articles(self.conn,[dict(id=1,type='story',title='Completely changed subject',
            url='https://example.com/changed',time=self.now,score=40)])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topic_labels WHERE id=1').fetchone()[0],0)
        self.conn.execute('INSERT OR REPLACE INTO story_topics VALUES (2,1002,.4,.1)')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topic_labels WHERE id=2').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topic_memberships WHERE topic=1001').fetchone()[0],0)

    def test_taxonomy_mismatch_fails_before_writing(self):
        self.conn.execute('INSERT INTO topic_registry VALUES (9999,?,0,0)',(np.zeros(512,'f4').tobytes(),))
        with self.assertRaisesRegex(ValueError,'Retrain'):
            refresh.classify(self.conn,self.now)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],0)

    def test_prediction_failure_does_not_commit_refresh_marker(self):
        with patch.object(self.model,'predict',side_effect=ValueError('corrupt model')):
            with self.assertRaisesRegex(ValueError,'corrupt'):
                refresh.refresh(self.conn,self.now,fetch=lambda c,n:None,embed=lambda c,n:None,discover_topics=False)
        self.assertIsNone(self.conn.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone())
