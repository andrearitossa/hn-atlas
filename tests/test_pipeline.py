import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import daily
import embed
import hn_sync
import production
import quality
import routing
import topics


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(f'{self.tmp.name}/test.db')
        self.addCleanup(self.conn.close)
        self.conn.executescript(hn_sync.SCHEMA + topics.SCHEMA + embed.SCHEMA + ";" + production.SCHEMA + routing.SCHEMA)
        self.model = f'{self.tmp.name}/model.npz'
        np.savez(self.model, mean=np.zeros(3,dtype='f4'), centroids=np.eye(3,dtype='f4')[:2], input_version=1)
        self.patch = patch.object(production, 'MODEL_PATH', self.model)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.now = 1790326180
        for ident in (0,1):
            self.conn.execute('INSERT INTO topics VALUES (?,?,?,?,?,?,?)',(ident,f'Topic {ident}','Subject',0,0,.5,.5))
            self.conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                              (ident,np.eye(3,dtype='f4')[ident].tobytes(),self.now-100*86400))
        self.conn.commit()

    def story(self, ident, title='Rust guide', url=None, text=None, score=10, when=None, vector=None):
        url = url if url is not None else f'https://site{ident%3}.example/article/{ident}'
        hn_sync.save_articles(self.conn,[dict(id=ident,type='story',title=title,url=url,text=text,score=score,
                                             time=self.now if when is None else when)])
        if vector is not None:
            self.conn.execute('INSERT INTO embeddings(id,model,vec) VALUES (?,?,?)',
                              (ident,embed.MODEL,np.array(vector,dtype='f4').tobytes()))
        self.conn.commit()

    def test_subject_inputs_separate_format_and_submission_commentary(self):
        self.assertEqual(routing.subject_text('Show HN: Rust guide (2024)','https://x.example/a','Comment'), 'Rust guide')
        self.assertEqual(routing.subject_text('Rust guide','https://y.example/a','Different'), 'Rust guide')
        self.assertEqual(routing.metadata('Show HN: Rust guide','https://x.example/a')[1], 'show')
        self.assertIn('Question body',routing.subject_text('Ask HN: Rust?','', '<p>Question body</p>'))
        self.assertEqual(routing.subject_title('Python 3.14'), 'Python 3.14')

    def test_canonical_identity_preserves_content_parameters(self):
        a=routing.metadata('A','https://x.example/article?id=1&utm_source=hn#top')[0]
        b=routing.metadata('B','https://x.example/article?id=1')[0]
        self.assertEqual(a,b)
        self.assertNotEqual(a,routing.metadata('B','https://x.example/article?id=2')[0])
        self.assertNotEqual(routing.metadata('A','https://x.example/')[0],routing.metadata('B','https://x.example/')[0])

    def test_only_weak_or_ambiguous_stories_wait(self):
        for i in range(1,5):self.story(i,score=200 if i==4 else 10)
        routing.store(self.conn,[(1,0,.2,.1),(2,0,.5,.001),(3,0,.8,.3),(4,0,.8,.3)],self.now)
        self.assertEqual(self.conn.execute('SELECT id FROM story_topics').fetchall(),[(3,),(4,)])
        self.assertEqual(dict(self.conn.execute('SELECT id,reason FROM classification_queue')),
                         {1:'low_fit',2:'ambiguous'})

    def test_single_center_classification(self):
        self.conn.execute('DELETE FROM topic_registry WHERE id=1')
        result=production.classify(self.conn,[42],np.array([[1.,0.,0.]]))
        self.assertEqual(result[0][:2],(42,0))
        self.assertGreater(result[0][3],0)

    def test_review_rejects_unknown_duplicate_and_missing_decisions(self):
        for value in [{'assignments':[]}, {'assignments':[{'id':1,'topic':99}]},
                      {'assignments':[{'id':1}]}, {'assignments':[{'id':True,'topic':0}]}]:
            with patch.object(routing,'ask_json',return_value=value):
                with self.assertRaises(ValueError):
                    routing.semantic_decisions([{'id':0}], [{'id':1}])

    def test_review_uses_semantics_and_reuses_duplicate_decision(self):
        for i in (1,2):self.story(i,url='https://x.example/same',vector=[1,0,0])
        routing.store(self.conn,[(1,0,.2,.001),(2,0,.2,.001)],self.now)
        self.conn.commit()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}), patch.object(routing,'ask_json',return_value={'assignments':[{'id':1,'topic':1}]}) as request:
            self.assertEqual(routing.review_pending(self.conn,now=self.now),2)
        self.assertEqual(request.call_count,1)
        self.assertEqual(self.conn.execute('SELECT id,topic FROM story_topics ORDER BY id').fetchall(),[(1,1),(2,1)])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM classification_queue').fetchone()[0],0)
        # The reviewed topic's actual score is stored, not the rejected suggestion's.
        self.assertEqual(self.conn.execute('SELECT sim FROM story_topics WHERE id=1').fetchone()[0],0)

    def test_no_key_defers_review_and_null_is_not_a_topic(self):
        self.story(1,vector=[1,0,0])
        routing.store(self.conn,[(1,0,.2,.001)],self.now);self.conn.commit()
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}),patch.object(routing,'ask_json') as ask:
            self.assertEqual(routing.review_pending(self.conn,now=self.now),0);ask.assert_not_called()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(routing,'ask_json',return_value={'assignments':[{'id':1,'topic':None}]}):
            self.assertEqual(routing.review_pending(self.conn,now=self.now),0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT reason,attempts FROM classification_queue').fetchone(),('unclassified',1))

    def test_embed_v2_reuses_normalized_input(self):
        self.story(1,title='Show HN: Rust guide (2024)',text='one')
        self.story(2,title='Rust guide',text='two')
        vector=np.ones(embed.DIM,dtype='f4').tobytes()
        with patch.object(embed,'embed_batch',return_value=[vector]) as call:
            self.assertEqual(embed.embed_pending(self.conn,version=2),2)
            call.assert_called_once_with(['Rust guide'])
            self.assertEqual(embed.embed_pending(self.conn,version=2),0)
        self.assertEqual(self.conn.execute('SELECT distinct input_version FROM embeddings').fetchall(),[(2,)])

    def test_empty_subject_is_skipped_and_retried_after_content_edit(self):
        self.story(1,title='(2023)',url='https://example.com/post')
        with patch.object(embed,'embed_batch') as call:
            self.assertEqual(embed.embed_pending(self.conn,version=2),0)
            self.assertEqual(embed.embed_pending(self.conn,version=2),0)
            call.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT reason FROM embedding_errors').fetchone()[0],'empty_subject')
        hn_sync.save_articles(self.conn,[dict(id=1,type='story',title='Rust compiler',
            url='https://example.com/post',time=self.now)])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM embedding_errors').fetchone()[0],0)
        with patch.object(embed,'embed_batch',return_value=[np.ones(embed.DIM,dtype='f4').tobytes()]):
            self.assertEqual(embed.embed_pending(self.conn,version=2),1)

    def test_content_refresh_invalidates_but_score_refresh_does_not(self):
        self.story(1,vector=[1,0,0]);routing.store(self.conn,[(1,0,.8,.3)],self.now);self.conn.commit()
        self.story_update(score=20)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM embeddings').fetchone()[0],1)
        self.story_update(title='A completely different subject')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM embeddings').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],0)

    def story_update(self,title='Rust guide',score=10):
        hn_sync.save_articles(self.conn,[dict(id=1,type='story',title=title,url='https://site1.example/article/1',
                                             score=score,time=self.now)])

    def test_new_topics_require_independent_sustained_support(self):
        x=np.tile([0.,0.,1.],(40,1));times=[self.now-10*86400]*20+[self.now-86400]*20
        self.assertTrue(production.supported_candidate(x,times,['a','b','c','d']*10,list(range(40)),self.now))
        self.assertFalse(production.supported_candidate(x,times,['a']*40,list(range(40)),self.now))
        self.assertFalse(production.supported_candidate(x,times,['a','b']*20,[1]*40,self.now))
        self.assertFalse(production.supported_candidate(x,[self.now]*40,['a','b','c','d']*10,list(range(40)),self.now))

    def test_novel_topic_is_not_published_on_first_observation(self):
        for i in range(40):self.story(i+10,when=self.now-(10 if i<20 else 1)*86400,vector=[0,0,1])
        rows,x=production.recent_vectors(self.conn,self.now-28*86400)
        _,ids,centers=production.model(self.conn)
        with patch.object(production,'name_topic') as name:
            self.assertEqual(production.births(self.conn,ids,centers,np.full(40,-1),rows,x,self.now),[])
            name.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM topic_candidates').fetchone()[0],1)
        later=self.now+7*86400
        for i in range(20):self.story(i+100,when=later-86400,vector=[0,0,1])
        rows,x=production.recent_vectors(self.conn,later-28*86400)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(production,'name_topic',return_value={'name':'New subject','description':'Coherent new subject','is_subject':True}):
            events=production.births(self.conn,ids,centers,np.full(len(rows),-1),rows,x,later)
        self.assertEqual(events,[('birth',2)])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topics WHERE topic=2').fetchone()[0],60)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM subscriptions').fetchone()[0],0)

    def test_initial_build_refuses_existing_registry_and_model(self):
        with self.assertRaises(ValueError):topics.main(f'{self.tmp.name}/test.db',self.model)
        with self.assertRaises(ValueError):topics.main(f'{self.tmp.name}/test.db',f'{self.tmp.name}/new.npz')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM topics').fetchone()[0],2)

    def test_model_selection_reports_chronological_holdout_tradeoff(self):
        rng=np.random.default_rng(8)
        x=np.vstack([rng.normal([1,0,0],.05,(100,3)),rng.normal([0,1,0],.05,(100,3))]).astype('f4')
        _,centers,report=topics.select_model(x,x[[0,101]],candidates=(2,3),tolerance=.02)
        self.assertEqual(len(report['trials']),2)
        self.assertEqual(report['chosen_k'],len(centers))
        self.assertEqual(report['chosen_k'],2)

    def test_quality_review_prioritizes_observed_weak_topics(self):
        self.conn.executescript(quality.SCHEMA)
        for i in range(20):
            self.story(i+1)
            self.conn.execute('INSERT INTO story_topics VALUES (?,?,?,?)',(i+1,i%2,.1 if i%2 else .9,.001 if i%2 else .2))
        self.assertEqual(quality.weak_ids(self.conn,self.now,limit=1),[1])

    def test_consolidation_merges_synonyms_and_rejects_missing_groups(self):
        centers=np.array([[1.,0.,0.],[.9,.1,0.],[0.,1.,0.]])
        names=[dict(name=n,description=n) for n in ['Rust Ecosystem','Rust Programming','Python']]
        result={'groups':[{'ids':[0,1],'name':'Rust','description':'Rust development'},
                          {'ids':[2],'name':'Python','description':'Python development'}]}
        with patch.object(topics,'ask_json',return_value=result):
            merged,labels,_=topics.consolidate_subjects(centers,names,[['title']]*3,[10,20,30])
        self.assertEqual(len(merged),2)
        self.assertEqual(labels[0]['name'],'Rust')
        result['groups'].pop()
        with patch.object(topics,'ask_json',return_value=result):
            with self.assertRaises(ValueError):topics.consolidate_subjects(centers,names,[['title']]*3,[10,20,30])

    def test_score_increase_does_not_queue_confident_assignment(self):
        self.story(1,vector=[1,0,0])
        routing.store(self.conn,[(1,0,.8,.3)],self.now);self.conn.commit()
        self.story_update(score=200)
        routing.store(self.conn,[(1,0,.8,.3)],self.now)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM classification_queue').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT topic FROM story_topics').fetchone()[0],0)

    def test_v2_external_commentary_edit_keeps_subject_embedding(self):
        self.story(1,text='Initial note')
        vector=np.ones(embed.DIM,dtype='f4').tobytes()
        with patch.object(embed,'embed_batch',return_value=[vector]):
            embed.embed_pending(self.conn,version=2)
        hn_sync.save_articles(self.conn,[dict(id=1,type='story',title='Rust guide',url='https://site1.example/article/1',
                                             text='A different submission note',score=10,time=self.now)])
        self.assertEqual(self.conn.execute('SELECT vec FROM embeddings').fetchone()[0],vector)

    def test_discovery_window_excludes_future_stories(self):
        self.story(1,when=self.now,vector=[1,0,0])
        self.story(2,when=self.now+86400,vector=[1,0,0])
        rows,_=production.recent_vectors(self.conn,self.now-86400,until=self.now)
        self.assertEqual([r[0] for r in rows],[1])
