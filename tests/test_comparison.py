from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import api_usage
import embed
import hn_sync
import topics
from scripts import compare_history as study


class ComparisonTests(unittest.TestCase):
    def test_final_initial_model_refits_on_available_validation_period(self):
        import sqlite3
        conn=sqlite3.connect(':memory:');self.addCleanup(conn.close)
        conn.executescript(hn_sync.SCHEMA)
        conn.execute('CREATE TABLE embeddings(id INTEGER PRIMARY KEY,vec BLOB,input_version INTEGER,input_hash TEXT)')
        vectors=[]
        for i in range(100):
            vector=np.array([float(i%2),float(i>=80),1.],dtype='f4')
            vectors.append(vector)
            conn.execute('INSERT INTO stories(id,time,title,fetched_at) VALUES (?,?,?,0)',(i,study.START+i,f'Subject {i}'))
            conn.execute('INSERT INTO embeddings VALUES (?,?,2,?)',(i,vector.tobytes(),str(i)))
        mean,_,report=topics.fit_model(conn,sample=200,candidates=(2,))
        np.testing.assert_allclose(mean,np.stack(vectors).mean(0),atol=1e-6)
        self.assertEqual(report['refit_stories'],100)

    def test_shared_embeddings_do_not_expose_future_stories_to_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'study';out.mkdir()
            source=root/'source.db'
            conn=hn_sync.open_db(str(source))
            hn_sync.save_articles(conn,[dict(id=i,type='story',title=f'Subject {i}',time=t)
                for i,t in enumerate([study.START-1,study.START,study.SPLIT],1)])
            conn.commit()
            conn.close()
            manifest=study.prepare(source,out)
            self.assertEqual(manifest['stories'],2)
            with patch.object(embed,'embed_batch',side_effect=lambda texts:
                              [np.ones(embed.DIM,dtype='f4').tobytes() for _ in texts]):
                study.embed_all(out,workers=2)
            stream=study.init_db(out,'stream',study.SPLIT)
            self.assertEqual(stream.execute('SELECT id FROM stories').fetchall(),[(2,)])
            self.assertEqual(stream.execute('SELECT id FROM embeddings').fetchall(),[(2,)])
            study.ingest(stream,study.SPLIT,study.SPLIT+1)
            self.assertEqual(stream.execute('SELECT id FROM embeddings ORDER BY id').fetchall(),[(2,),(3,)])
            stream.close()

    def test_concurrent_api_reservations_cannot_overrun_ceiling(self):
        old=api_usage._ledger,api_usage._ceiling
        try:
            with tempfile.TemporaryDirectory() as tmp:
                api_usage.configure(str(Path(tmp)/'usage.db'),.05)
                def reserve(_):
                    try:
                        return api_usage.reserve('gpt-5.6-terra',['x'],1000)
                    except api_usage.BudgetExceeded:
                        return None
                with ThreadPoolExecutor(8) as pool:
                    results=list(pool.map(reserve,range(8)))
                self.assertEqual(sum(v is not None for v in results),4)
                self.assertLessEqual(api_usage._ledger.execute('SELECT sum(cost) FROM calls').fetchone()[0],.05)
                api_usage._ledger.close()
        finally:
            api_usage._ledger,api_usage._ceiling=old
