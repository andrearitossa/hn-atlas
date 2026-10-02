import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from embed import DIM, MODEL
from search_export import cutoff, write


def stamp(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())


class SearchExportTests(unittest.TestCase):
    def test_calendar_month_cutoff_clamps_day(self):
        self.assertEqual(cutoff(stamp('2026-05-31')), stamp('2026-02-28'))
        self.assertEqual(cutoff(stamp('2026-01-02')), stamp('2025-10-02'))

    def test_only_recent_live_posts_and_compatible_vectors_leave_database(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        self.addCleanup(conn.close)
        conn.executescript('CREATE TABLE stories(id,title,url,time,score,descendants,dead,deleted);CREATE TABLE embeddings(id,model,vec);')
        now=stamp('2026-10-02');since=cutoff(now)
        for ident,time,dead,deleted in [(1,since,0,0),(2,since-1,0,0),(3,now,1,0),(4,now,0,1),(5,now+1,0,0),(6,now,0,0)]:
            conn.execute('INSERT INTO stories VALUES(?,?,?,?,?,?,?,?)',(ident,'Title','https://example.org',time,10,2,dead,deleted))
        vector=np.zeros(DIM,dtype='<f4');vector[0]=1
        conn.execute('INSERT INTO embeddings VALUES(?,?,?)',(1,MODEL,vector.tobytes()))
        conn.execute('INSERT INTO embeddings VALUES(?,?,?)',(6,'wrong-model',vector.tobytes()))
        with TemporaryDirectory() as directory:
            result=write(conn,directory,now)
            self.assertEqual({p['id'] for p in result['posts']},{1,6})
            self.assertEqual(sum(s['count'] for s in result['shards']),1)
            self.assertEqual((Path(directory)/'search-data/vectors-0.bin').stat().st_size,DIM)
            self.assertEqual(next(p for p in result['posts'] if p['id']==6)['vector'],-1)
            self.assertNotIn('vec',json.loads((Path(directory)/'search-data/posts-0.json').read_text())[0])
