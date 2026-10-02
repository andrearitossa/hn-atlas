import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import numpy as np
import search_sync


class Cloud:
    config = dict(account_id='test', database_id='test', index_name='test')
    written = 0

    def __init__(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.executescript(search_sync.SCHEMA.read_text())
        self.uploads = [];self.deletes = [];self.sqls = [];self.fail = False

    def sql(self, sql):
        self.sqls.append(sql);self.db.executescript(sql)

    def upsert(self, rows):
        self.uploads.extend(rows)
        if self.fail:
            raise RuntimeError('Interrupted')

    def delete_vectors(self, ids):
        self.deletes.extend(ids)


def post(score=10, title='Rust', topic=3):
    vec = np.ones(512, dtype='<f4').tobytes()
    return dict(id=1,title=title,url="https://example.com/a'b",time=100,score=score,descendants=2,
                topics=[topic],vec=vec,vector_hash=title)


class SearchSyncTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.conn.execute('create table topics(id integer,name text)');self.conn.execute("insert into topics values(3,'Rust')")
        self.ledger=sqlite3.connect(':memory:');self.cloud=Cloud()
        self.addCleanup(self.conn.close);self.addCleanup(self.ledger.close);self.addCleanup(self.cloud.db.close)

    def run_sync(self, posts):
        with patch('search_sync.load_posts', return_value=iter(posts)):
            return search_sync.sync(self.conn,self.ledger,self.cloud,100)

    def test_new_posts_then_unchanged_then_numbers_only(self):
        self.run_sync([post()]);self.assertEqual(len(self.cloud.uploads),1)
        self.cloud.uploads.clear();self.cloud.sqls.clear()
        result=self.run_sync([post()]);self.assertEqual(result['rows_changed'],0);self.assertEqual(self.cloud.uploads,[])
        result=self.run_sync([post(score=20)]);self.assertEqual(result['rows_changed'],1);self.assertEqual(self.cloud.uploads,[])
        self.assertNotIn('post_topics',self.cloud.sqls[-2])
        self.assertEqual(self.cloud.db.execute('select score from posts').fetchone()[0],20)
        self.assertEqual(self.cloud.db.execute("select rowid from posts_fts where posts_fts match 'Rust'").fetchall(),[(1,)])

    def test_text_topics_and_expiry_update_both_indexes(self):
        self.run_sync([post()]);self.cloud.uploads.clear()
        self.run_sync([post(title='Python',topic=7)])
        self.assertEqual(self.cloud.deletes,[])
        self.assertEqual(self.cloud.uploads[0]['metadata'],{'topic1':7,'topic2':-1,'topic3':-1})
        self.assertEqual(self.cloud.db.execute("select rowid from posts_fts where posts_fts match 'Rust'").fetchall(),[])
        self.assertEqual(self.cloud.db.execute('select topic from post_topics').fetchone()[0],7)
        self.run_sync([])
        self.assertEqual(self.cloud.db.execute('select count(*) from posts').fetchone()[0],0)
        self.assertEqual(self.cloud.db.execute('select count(*) from post_topics').fetchone()[0],0)
        self.assertEqual(self.cloud.db.execute("select value from search_state where key='count'").fetchone()[0],0)
        self.assertEqual(self.cloud.deletes,['1'])

    def test_partial_remote_failure_does_not_advance_ledger_and_retry_is_safe(self):
        self.cloud.fail=True
        with self.assertRaises(RuntimeError):self.run_sync([post()])
        self.assertEqual(self.ledger.execute('select count(*) from synced').fetchone()[0],0)
        self.cloud.fail=False;self.run_sync([post()])
        self.assertEqual(self.cloud.db.execute('select count(*) from posts').fetchone()[0],1)
        self.assertEqual(self.ledger.execute('select count(*) from synced').fetchone()[0],1)

    def test_migration_deletes_namespace_copies_and_preserves_one_vector_per_post(self):
        self.run_sync([post()])
        self.ledger.execute("UPDATE synced SET vector_hash='old-format',vector_ids=?", (json.dumps(['1:all','1:topic-3']),))
        self.ledger.commit();self.cloud.uploads.clear()
        result=self.run_sync([post()])
        self.assertEqual(result['rows_changed'],0)
        self.assertEqual(len(self.cloud.uploads),1)
        self.assertEqual(self.cloud.uploads[0]['id'],'1')
        self.assertNotIn('namespace',self.cloud.uploads[0])
        self.assertEqual(set(self.cloud.deletes),{'1:all','1:topic-3'})
        self.assertEqual(json.loads(self.ledger.execute('select vector_ids from synced').fetchone()[0]),['1'])

    def test_three_memberships_still_upload_only_one_vector(self):
        p=post();p['topics']=[3,7,11]
        self.run_sync([p]);self.assertEqual(len(self.cloud.uploads),1)
        self.assertEqual(self.cloud.uploads[0]['metadata'],{'topic1':3,'topic2':7,'topic3':11})

    def test_sql_keeps_quotes_as_data(self):
        self.run_sync([post(title="Rust'; DROP TABLE posts; --")])
        self.assertEqual(self.cloud.db.execute('select title from posts').fetchone()[0],"Rust'; DROP TABLE posts; --")


if __name__ == '__main__':unittest.main()
