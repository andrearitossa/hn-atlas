import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import embed
import hn_sync
import topics
import routing
class SyncDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.conn=hn_sync.open_db(str(Path(self.tmp.name)/'test.db'))
        self.addCleanup(self.conn.close)
        self.conn.executescript(topics.TOPIC_SCHEMA+topics.SCHEMA+routing.SCHEMA+embed.SCHEMA+';')
        self.now=1790326180
        model=Path(self.tmp.name)/'model.npz'
        np.savez(model,mean=np.zeros(3,dtype='f4'),centroids=np.eye(3,dtype='f4')[:2],input_version=1)
        self.model_patch=patch.object(topics, 'MODEL_PATH',str(model))
        self.model_patch.start();self.addCleanup(self.model_patch.stop)
        for i in (0,1):
            self.conn.execute('INSERT INTO topics VALUES (?,?,?,?,0,.5,.5)',(i,f'Topic {i}','Subject',0))
            self.conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                              (i,np.eye(3,dtype='f4')[i].tobytes(),self.now-100*86400))
        self.conn.commit()

    def story(self,ident,topic,vector,age=1):
        hn_sync.save_articles(self.conn,[dict(id=ident,type='story',title=f'Subject {ident}',
            url=f'https://site{ident%3}.example/{ident}',score=50,time=self.now-age*86400)])
        self.conn.execute('INSERT INTO embeddings VALUES (?,?,?)',
                          (ident,embed.MODEL,np.array(vector,dtype='f4').tobytes()))
        self.conn.execute('INSERT INTO story_topics VALUES (?,?,.8,.3)',(ident,topic))
        self.conn.commit()


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

    def test_recent_refresh_commits_completed_batches_before_later_failure(self):
        self.conn.executemany(
            'INSERT INTO stories(id,type,time,title,score,fetched_at) VALUES (?,\'story\',?,\'Post\',1,0)',
            [(ident, self.now - 86400) for ident in range(1, 1002)])
        self.conn.commit()

        class Pool:
            def map(_, fn, ids):
                return map(fn, ids)

        def fetch(ident):
            if ident == 1001:
                raise ConnectionError('later batch failed')
            return dict(id=ident, type='story', time=self.now - 86400,
                        title='Post', score=ident, descendants=ident)

        with patch.object(hn_sync, 'fetch_item', side_effect=fetch), self.assertRaises(ConnectionError):
            hn_sync.refresh_recent(self.conn, Pool(), now=self.now, fetched_before=self.now)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT score,descendants FROM stories WHERE id=1000').fetchone(), (1000, 1000))
        self.assertEqual(self.conn.execute('SELECT score,descendants FROM stories WHERE id=1001').fetchone(), (1, None))


if __name__ == '__main__':
    unittest.main()
