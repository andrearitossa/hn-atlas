import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import embed
import hn_sync
import production
import routing
import structure
import topics
import weekly


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.conn=hn_sync.open_db(str(Path(self.tmp.name)/'test.db'))
        self.addCleanup(self.conn.close)
        self.conn.executescript(topics.SCHEMA+production.SCHEMA+routing.SCHEMA+embed.SCHEMA+';')
        self.now=1790326180
        model=Path(self.tmp.name)/'model.npz'
        np.savez(model,mean=np.zeros(3,dtype='f4'),centroids=np.eye(3,dtype='f4')[:2],input_version=1)
        self.model_patch=patch.object(production,'MODEL_PATH',str(model))
        self.model_patch.start();self.addCleanup(self.model_patch.stop)
        for i in (0,1):
            self.conn.execute('INSERT INTO topics VALUES (?,?,?,?,0,.5,.5)',(i,f'Topic {i}','Subject',0))
            self.conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                              (i,np.eye(3,dtype='f4')[i].tobytes(),self.now-100*86400))
        self.conn.commit()

    def story(self,ident,topic,vector,age=1):
        hn_sync.save_articles(self.conn,[dict(id=ident,type='story',title=f'Subject {ident}',
            url=f'https://site{ident%3}.example/{ident}',score=10,time=self.now-age*86400)])
        self.conn.execute('INSERT INTO embeddings VALUES (?,?,?)',
                          (ident,embed.MODEL,np.array(vector,dtype='f4').tobytes()))
        self.conn.execute('INSERT INTO story_topics VALUES (?,?,.8,.3)',(ident,topic))
        self.conn.commit()

    def test_reviews_once_per_calendar_week_even_with_timer_jitter(self):
        monday=1790553600  # 2026-09-28
        with patch.object(routing,'review_pending') as review,patch.object(production,'weekly',return_value=[]):
            weekly.maintain(self.conn,now=monday+600)
            weekly.maintain(self.conn,now=monday+86400)
            weekly.maintain(self.conn,now=monday+7*86400)
        self.assertEqual(review.call_count,2)

    def test_recent_refresh_skips_stories_already_fetched_in_this_run(self):
        self.story(1,0,[1,0,0],age=10);self.story(2,0,[1,0,0],age=2)
        self.story(3,0,[1,0,0],age=20)
        self.conn.execute('UPDATE stories SET fetched_at=?',(self.now-1,))
        self.conn.execute('UPDATE stories SET fetched_at=? WHERE id=2',(self.now,))
        self.conn.commit()
        class Pool:
            def map(_,fn,ids):
                return map(fn,ids)
        with patch.object(hn_sync,'fetch_item',return_value=dict(id=1,type='story',title='Subject 1',
                url='https://site1.example/1',time=self.now-10*86400,score=200,descendants=80)) as fetch:
            hn_sync.refresh_recent(self.conn,Pool(),now=self.now,fetched_before=self.now)
        fetch.assert_called_once_with(1)
        self.assertEqual(self.conn.execute('SELECT score,descendants FROM stories WHERE id=1').fetchone(),(200,80))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM embeddings').fetchone()[0],3)

    def test_merge_preserves_aliases_and_subscription_tokens(self):
        self.story(1,0,[1,0,0]);self.story(2,1,[1,0,0])
        self.conn.execute("INSERT INTO subscriptions VALUES ('token','x@example.com',1,'weekly',1,0,0)")
        self.conn.commit()
        rows,x=production.recent_vectors(self.conn,self.now-28*86400)
        proposal=[dict(kind='merge',topics=[0,1],members=[np.array([0]),np.array([1])])]
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(structure,'candidates',return_value=proposal),patch.object(structure,'ask_json',return_value={'decisions':[{'id':0,'approve':True}]}):
            events=structure.maintain(self.conn,*production.model(self.conn)[1:],rows,x,self.now)
        self.assertEqual(events,[('merge',1,0)])
        self.assertEqual(production.resolve_topic(self.conn,1),0)
        self.assertEqual(self.conn.execute('SELECT topic FROM subscriptions').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT DISTINCT topic FROM story_topics').fetchall(),[(0,)])

    def test_split_reclassifies_affected_stories_without_copying_subscriptions(self):
        self.story(1,0,[1,0,0]);self.story(2,0,[0,0,1])
        self.conn.execute("INSERT INTO subscriptions VALUES ('token','x@example.com',0,'weekly',1,0,0)")
        self.conn.commit()
        rows,x=production.recent_vectors(self.conn,self.now-28*86400)
        proposal=[dict(kind='split',topics=[0],members=[np.array([0]),np.array([1])])]
        result={'decisions':[dict(id=0,approve=True,names=[dict(name='A',description='A subject'),dict(name='B',description='B subject')])]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(structure,'candidates',return_value=proposal),patch.object(structure,'ask_json',return_value=result):
            events=structure.maintain(self.conn,*production.model(self.conn)[1:],rows,x,self.now)
        self.assertEqual(events,[('split',0,2)])
        self.assertEqual(self.conn.execute('SELECT id,topic FROM story_topics ORDER BY id').fetchall(),[(1,0),(2,2)])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM subscriptions').fetchone()[0],1)

    def test_bad_structural_response_cannot_partially_mutate_topics(self):
        self.story(1,0,[1,0,0])
        rows,x=production.recent_vectors(self.conn,0)
        proposal=[dict(kind='split',topics=[0],members=[np.array([0]),np.array([0])])]
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(structure,'candidates',return_value=proposal),patch.object(structure,'ask_json',return_value={'decisions':[dict(id=0,approve=True,names=[])]}):
            with self.assertRaises(ValueError):
                structure.maintain(self.conn,*production.model(self.conn)[1:],rows,x,self.now)
        self.assertEqual(self.conn.execute('SELECT name FROM topics WHERE id=0').fetchone()[0],'Topic 0')

    def test_old_uncertainty_cannot_consume_weekly_review_budget(self):
        self.story(1,0,[1,0,0],age=90)
        self.conn.execute('DELETE FROM story_topics')
        routing.store(self.conn,[(1,0,.1,.01)],self.now)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(routing,'ask_json') as ask:
            self.assertEqual(routing.review_pending(self.conn,now=self.now),0)
            ask.assert_not_called()

    def test_split_candidate_requires_two_sustained_distinct_groups(self):
        for i in range(160):
            self.story(i+1,0,[1 if i<80 else -1,0,0],age=1 if i%80<40 else 10)
        rows,x=production.recent_vectors(self.conn,self.now-28*86400)
        proposals=structure.candidates(self.conn,*production.model(self.conn)[1:],rows,x,self.now)
        self.assertEqual([p['kind'] for p in proposals],['split'])
        self.conn.execute('UPDATE stories SET time=?',(self.now-86400,))
        rows,x=production.recent_vectors(self.conn,self.now-28*86400)
        self.assertEqual(structure.candidates(self.conn,*production.model(self.conn)[1:],rows,x,self.now),[])

    def test_alias_subscriptions_receive_one_digest(self):
        self.story(1,0,[1,0,0])
        self.conn.execute("UPDATE topic_registry SET status='merged',parent=0 WHERE id=1")
        for token,topic in [('one',0),('two',1)]:
            self.conn.execute("INSERT INTO subscriptions VALUES (?, 'x@example.com',?,'weekly',1,0,0)",(token,topic))
        self.conn.commit()
        with patch.object(production.time,'time',return_value=self.now),patch.object(production,'send') as send:
            self.assertEqual(production.deliver(self.conn,'https://example.com'),1)
            self.assertEqual(production.deliver(self.conn,'https://example.com'),0)
            send.assert_called_once()
        self.assertEqual(self.conn.execute('SELECT DISTINCT last_sent FROM subscriptions').fetchall(),[(self.now,)])


if __name__ == '__main__':
    unittest.main()
