import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import api_usage
import embed
import hn_sync
import production
from scripts import replay_history


class ReplayTests(unittest.TestCase):
    def test_snapshot_and_arrival_boundary_do_not_import_future_or_private_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.db'
            conn = hn_sync.open_db(str(source))
            hn_sync.save_articles(conn,[dict(id=i,type='story',title=f'Story {i}',time=t)
                for i,t in enumerate([replay_history.SPLIT-1,replay_history.SPLIT,replay_history.SPLIT+86400],1)])
            conn.execute('CREATE TABLE subscriptions(email TEXT)')
            conn.execute("INSERT INTO subscriptions VALUES ('private@example.test')")
            conn.commit();conn.close()
            out=root/'run';out.mkdir()
            manifest=replay_history.prepare(source,out)
            self.assertEqual((manifest['initial_stories'],manifest['streamed_stories']),(1,2))
            work=hn_sync.open_db(str(out/'replay.db'))
            work.execute('ATTACH DATABASE ? AS archive',((out/'snapshot.db').as_uri()+'?mode=ro',))
            replay_history.ingest(work,0,replay_history.SPLIT)
            self.assertEqual(work.execute('SELECT id FROM stories').fetchall(),[(1,)])
            replay_history.ingest(work,replay_history.SPLIT,replay_history.SPLIT+86400)
            self.assertEqual(work.execute('SELECT id FROM stories ORDER BY id').fetchall(),[(1,),(2,)])
            self.assertFalse(work.execute("SELECT 1 FROM archive.sqlite_master WHERE name='subscriptions'").fetchone())
            work.close()

    def test_embedding_bounds_include_old_history_but_exclude_future(self):
        conn=sqlite3.connect(':memory:');self.addCleanup(conn.close)
        conn.executescript(hn_sync.SCHEMA)
        for i,t in [(1,replay_history.SPLIT-1),(2,replay_history.SPLIT)]:
            conn.execute('INSERT INTO stories(id,title,time,fetched_at) VALUES (?,?,?,0)',(i,f'Subject {i}',t))
        with patch.object(embed,'embed_batch',return_value=[np.ones(embed.DIM,dtype='f4').tobytes()]):
            self.assertEqual(embed.embed_pending(conn,version=2,since=0,until=replay_history.SPLIT),1)
        self.assertEqual(conn.execute('SELECT id FROM embeddings').fetchall(),[(1,)])

    def test_registry_and_audits_use_replay_clock(self):
        conn=sqlite3.connect(':memory:');self.addCleanup(conn.close)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model.npz'
            np.savez(path,centroids=np.eye(2))
            with patch.object(production,'MODEL_PATH',str(path)):
                production.setup(conn,now=replay_history.SPLIT)
            self.assertEqual(conn.execute('SELECT DISTINCT born FROM topic_registry').fetchall(),[(replay_history.SPLIT,)])
            production.mark_audited(conn,now=replay_history.SPLIT)
            self.assertFalse(production.audit_due(conn,now=replay_history.SPLIT+6*86400))
            self.assertTrue(production.audit_due(conn,now=replay_history.SPLIT+7*86400))

    def test_api_ceiling_survives_restart_and_failed_request_reservations(self):
        old=(api_usage._ledger,api_usage._ceiling)
        try:
            with tempfile.TemporaryDirectory() as temp:
                path=str(Path(temp)/'usage.db')
                api_usage.configure(path,.11)
                api_usage.reserve('gpt-5.6-terra',['test'],8192)
                api_usage._ledger.close()
                api_usage.configure(path,.11)
                with self.assertRaises(api_usage.BudgetExceeded):
                    api_usage.reserve('gpt-5.6-terra',['test'],8192)
                api_usage._ledger.close()
        finally:
            api_usage._ledger,api_usage._ceiling=old


if __name__ == '__main__':
    unittest.main()
