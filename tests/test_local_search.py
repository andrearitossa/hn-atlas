import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from embed import DIM, MODEL
from local_search.search import search


def vector(x, y):
    v = np.zeros(DIM, dtype='<f4')
    v[:2] = [x, y]
    return v


class SemanticSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
        CREATE TABLE stories(id INTEGER PRIMARY KEY,title,url,by,time,score,descendants,dead DEFAULT 0,deleted DEFAULT 0);
        CREATE TABLE embeddings(id INTEGER PRIMARY KEY,model,vec);
        CREATE TABLE topic_registry(id INTEGER PRIMARY KEY,centroid);
        ''')
        for ident, v in [(1, vector(1, 0)), (2, vector(.99, .14)), (3, vector(0, 1))]:
            self.conn.execute('INSERT INTO stories(id,title,time) VALUES(?,?,?)', (ident, str(ident), 1))
            self.conn.execute('INSERT INTO embeddings VALUES(?,?,?)', (ident, MODEL, v.tobytes()))
        self.conn.execute('INSERT INTO topic_registry VALUES(?,?)', (10, vector(0, 1).tobytes()))

    def test_query_leads_and_topic_changes_order_without_excluding(self):
        plain = search(self.conn, vector(1, 0))
        context = search(self.conn, vector(1, 0), topic_id=10)
        self.assertEqual([p['id'] for p in plain['posts']], [1, 2, 3])
        self.assertEqual([p['id'] for p in context['posts']], [2, 1, 3])
        self.assertEqual(context['searched_vectors'], 3)
        self.assertAlmostEqual(context['posts'][0]['search_score'], context['posts'][0]['query_similarity'] + .15 * context['posts'][0]['topic_similarity'])

    def test_live_compatible_vectors_only(self):
        self.conn.execute('UPDATE stories SET deleted=1 WHERE id=1')
        self.conn.execute("UPDATE embeddings SET model='other' WHERE id=2")
        self.assertEqual([p['id'] for p in search(self.conn, vector(1, 0))['posts']], [3])

    def test_top_k_across_batches(self):
        self.conn.executemany('INSERT INTO stories(id,title,time) VALUES(?,?,?)', [(i, str(i), 1) for i in range(4, 4105)])
        self.conn.executemany('INSERT INTO embeddings VALUES(?,?,?)', [(i, MODEL, vector(0, 1).tobytes()) for i in range(4, 4105)])
        result = search(self.conn, vector(1, 0), limit=2)
        self.assertEqual([p['id'] for p in result['posts']], [1, 2])
        self.assertEqual(result['searched_vectors'], 4104)

    def test_unknown_topic_rejected(self):
        with self.assertRaises(ValueError):
            search(self.conn, vector(1, 0), topic_id=999)

    def test_local_server_access(self):
        import asyncio
        from starlette.requests import Request
        from starlette.responses import Response
        from local_search.server import local_only, index

        async def check():
            async def success(request):
                return Response('ok')
            for host, origin, expected in [('127.0.0.1:8001', '', 200), ('external.example', '', 403), ('127.0.0.1:8001', 'https://external.example', 403)]:
                headers = [(b'host', host.encode())]
                if origin:
                    headers.append((b'origin', origin.encode()))
                request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'scheme': 'http', 'root_path': '', 'query_string': b'', 'headers': headers, 'server': ('127.0.0.1', 8001), 'client': ('127.0.0.1', 12345)})
                self.assertEqual((await local_only(request, success)).status_code, expected)
            self.assertTrue(Path(index().path).is_file())
        asyncio.run(check())
