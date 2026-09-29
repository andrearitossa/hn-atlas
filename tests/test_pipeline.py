import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import embed
import hn_sync
import topics
import routing


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(f'{self.tmp.name}/test.db')
        self.addCleanup(self.conn.close)
        self.conn.executescript(hn_sync.SCHEMA + topics.TOPIC_SCHEMA + topics.SCHEMA + embed.SCHEMA + ";" + topics.SCHEMA + routing.SCHEMA)
        self.model = f'{self.tmp.name}/model.npz'
        np.savez(self.model, mean=np.zeros(3,dtype='f4'), centroids=np.eye(3,dtype='f4')[:2], input_version=1)
        self.patch = patch.object(topics, 'MODEL_PATH', self.model)
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
        self.assertEqual(routing.subject_text('Show HN: Rust guide (2024)','https://x.example/a','Comment'), 'Rust guide\nComment')
        self.assertEqual(routing.subject_text('Rust guide','https://y.example/a','Different'), 'Rust guide\nDifferent')
        self.assertEqual(routing.metadata('Show HN: Rust guide','https://x.example/a')[1], 'show')
        self.assertIn('Question body',routing.subject_text('Ask HN: Rust?','', '<p>Question body</p>'))
        self.assertEqual(routing.subject_title('Python 3.14'), 'Python 3.14')

    def test_canonical_identity_preserves_content_parameters(self):
        a=routing.metadata('A','https://x.example/article?id=1&utm_source=hn#top')[0]
        b=routing.metadata('B','https://x.example/article?id=1')[0]
        self.assertEqual(a,b)
        self.assertNotEqual(a,routing.metadata('B','https://x.example/article?id=2')[0])
        self.assertNotEqual(routing.metadata('A','https://x.example/')[0],routing.metadata('B','https://x.example/')[0])


    def test_review_rejects_unknown_duplicate_and_missing_decisions(self):
        for value in [{'assignments':[]}, {'assignments':[{'id':1,'topic':99}]},
                      {'assignments':[{'id':1}]}, {'assignments':[{'id':True,'topic':0}]}]:
            with patch.object(routing,'ask_json',return_value=value):
                with self.assertRaises(ValueError):
                    routing.semantic_decisions([{'id':0}], [{'id':1}])

    def test_review_retries_an_incomplete_response_without_losing_a_story(self):
        complete={'assignments':[{'id':1,'topic':0},{'id':2,'topic':None}]}
        with patch.object(routing,'ask_json',side_effect=[{'assignments':[{'id':1,'topic':0}]},complete]) as ask:
            result=routing.semantic_decisions([{'id':0}],[{'id':1},{'id':2}])
        self.assertEqual(result,complete['assignments'])
        self.assertEqual(ask.call_count,2)

    def test_review_splits_incomplete_large_batches_and_preserves_every_id(self):
        stories=[{'id':i} for i in range(40)]
        left={'assignments':[{'id':i,'topic':0} for i in range(20)]}
        right={'assignments':[{'id':i,'topic':None} for i in range(20,40)]}
        with patch.object(routing,'ask_json',side_effect=[left,left,right]) as ask:
            result=routing.semantic_decisions([{'id':0}],stories)
        self.assertEqual(result,left['assignments']+right['assignments'])
        self.assertEqual(ask.call_count,3)


    def test_embed_v2_reuses_normalized_input(self):
        self.story(1,title='Show HN: Rust guide (2024)',text='same body')
        self.story(2,title='Rust guide',text='same body')
        vector=np.ones(embed.DIM,dtype='f4').tobytes()
        with patch.object(embed,'embed_batch',return_value=[vector]) as call:
            self.assertEqual(embed.embed_pending(self.conn,version=2),2)
            call.assert_called_once_with(['Rust guide\nsame body'])
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
        self.story(1,vector=[1,0,0]);self.conn.execute('INSERT INTO story_topics VALUES(1,0,.8,.3)');self.conn.commit()
        self.story_update(score=20)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM embeddings').fetchone()[0],1)
        self.story_update(title='A completely different subject')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM embeddings').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],0)

    def story_update(self,title='Rust guide',score=10):
        hn_sync.save_articles(self.conn,[dict(id=1,type='story',title=title,url='https://site1.example/article/1',
                                             score=score,time=self.now)])


    def test_post_body_edit_invalidates_subject_embedding(self):
        self.story(1,text='Initial note')
        vector=np.ones(embed.DIM,dtype='f4').tobytes()
        with patch.object(embed,'embed_batch',return_value=[vector]):
            embed.embed_pending(self.conn,version=2)
        hn_sync.save_articles(self.conn,[dict(id=1,type='story',title='Rust guide',url='https://site1.example/article/1',
                                             text='A different submission note',score=10,time=self.now)])
        self.assertIsNone(self.conn.execute('SELECT vec FROM embeddings').fetchone())

    def test_changed_old_post_is_embedded_outside_recent_window(self):
        self.story(1, text='Initial body', when=self.now - 200 * 86400)
        vector = np.ones(embed.DIM, dtype='f4').tobytes()
        with patch.object(embed, 'embed_batch', return_value=[vector]):
            embed.embed_pending(self.conn, version=2, since=0)
        hn_sync.save_articles(self.conn, [dict(id=1, type='story', title='Rust guide',
            url='https://site1.example/article/1', text='Updated body',
            score=20, time=self.now - 200 * 86400)])
        with patch.object(embed, 'embed_batch', return_value=[vector]) as call:
            self.assertEqual(embed.embed_pending(self.conn, version=2, since=self.now - 95 * 86400), 1)
            call.assert_called_once_with(['Rust guide\nUpdated body'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM dirty_stories').fetchone()[0], 0)

    def test_changed_input_hash_refreshes_stored_vector_and_assignment(self):
        self.story(1, text='Original body')
        vector = np.ones(embed.DIM, dtype='f4').tobytes()
        with patch.object(embed, 'embed_batch', return_value=[vector]):
            embed.embed_pending(self.conn, version=2)
        self.conn.execute('INSERT INTO story_topics VALUES (1,0,.8,.3)')
        self.conn.execute("UPDATE stories SET text='New body' WHERE id=1")
        self.conn.commit()
        with patch.object(embed, 'embed_batch', return_value=[vector]) as call:
            self.assertEqual(embed.embed_pending(self.conn, version=2), 1)
            call.assert_called_once_with(['Rust guide\nNew body'])
            self.assertEqual(embed.embed_pending(self.conn, version=2), 0)
        self.assertIsNone(self.conn.execute('SELECT id FROM story_topics').fetchone())
