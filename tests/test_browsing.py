import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from database import connect
import hn_sync
import topics
import server


class BrowsingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = f'{self.directory.name}/hn.db'
        self.clock = int(time.time()) - 90 * server.DAY
        with connect(self.path) as c:
            c.executescript(hn_sync.SCHEMA + topics.SCHEMA + '''
                CREATE TABLE topics(id INTEGER PRIMARY KEY, name TEXT, description TEXT,
                                    size INTEGER, x REAL, y REAL);
                CREATE TABLE story_topics(id INTEGER PRIMARY KEY,topic INTEGER,sim REAL);
                INSERT INTO topics VALUES(3,'Current','',99,0,0);
                INSERT INTO topic_registry(id,centroid,born) VALUES(3,x'0000803f',0);
            ''')
            for ident, title, age, dead, deleted in [
                (1, 'Rust 100% useful', 0, 0, 0), (2, 'Rust guide', 0, 0, 0),
                (3, 'Old story', 40, 0, 0), (4, 'Deleted', 0, 0, 1), (5, 'Dead', 0, 1, 0),
            ]:
                c.execute('INSERT INTO stories(id,title,url,time,score,descendants,dead,deleted,fetched_at) '
                          'VALUES(?,?,?,?,10,2,?,?,0)',
                          (ident, title, 'https://example.org', self.clock-age*server.DAY, dead, deleted))
                c.execute('INSERT INTO story_topics VALUES(?,3,.8)', (ident,))
        self.db_patch = patch.object(server, 'DB', self.path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        server.topic_list.cache_clear()
        self.addCleanup(server.topic_list.cache_clear)

    def test_only_current_topic_ids_work(self):
        self.assertEqual(server.topic(3)['id'], 3)
        with self.assertRaises(HTTPException):
            server.topic(1)

    def test_pagination_has_stable_ties_and_an_end(self):
        first = server.stories(3, limit=2)
        self.assertEqual([p['id'] for p in first['posts']], [2, 1])
        second = server.stories(3, limit=2, offset=first['next_offset'])
        self.assertEqual([p['id'] for p in second['posts']], [3])
        self.assertIsNone(second['next_offset'])

    def test_http_query_validation(self):
        async def request(query, expected):
            messages = []

            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}

            async def send(message):
                messages.append(message)

            await server.app({
                'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
                'method': 'GET', 'scheme': 'http', 'path': '/api/topics/3/stories',
                'query_string': query.encode(), 'root_path': '', 'headers': [],
                'server': ('test', 80), 'client': ('test', 1234),
            }, receive, send)
            self.assertEqual(messages[0]['status'], expected)
            return json.loads(b''.join(m.get('body', b'') for m in messages))

        data = asyncio.run(request('limit=2', 200))
        self.assertEqual(data['id'], 3)
        self.assertEqual(len(data['posts']), 2)
        for query in ('limit=101', 'offset=-1', 'days=-1', 'sort=invalid', 'q=' + 'a'*201):
            asyncio.run(request(query, 422))

    def test_search_filters_title_url_and_literal_wildcards(self):
        self.assertEqual(len(server.stories(3, q='rUsT')['posts']), 2)
        self.assertEqual(len(server.stories(3, q='example.org')['posts']), 3)
        self.assertEqual([p['id'] for p in server.stories(3, q='%')['posts']], [1])
        self.assertEqual(server.stories(3, q="' OR 1=1 --")['posts'], [])
        self.assertEqual(len(server.stories(3, days=7)['posts']), 2)

    def test_archive_year_uses_utc_boundaries(self):
        with connect(self.path) as c:
            c.execute('UPDATE stories SET time=1704067199 WHERE id=1')
            c.execute('UPDATE stories SET time=1704067200 WHERE id=2')
            c.execute('UPDATE stories SET time=1735689600 WHERE id=3')
        self.assertEqual([p['id'] for p in server.stories(3, year=2024)['posts']], [2])
        self.assertEqual([p['id'] for p in server.stories(3, year=2023)['posts']], [1])
        self.assertEqual([p['id'] for p in server.stories(3, year=2025)['posts']], [3])
        self.assertEqual(server.stories(3, year=2024, q='Old')['posts'], [])

    def test_deleted_stories_excluded_from_counts_and_results(self):
        detail = server.topic(3)
        self.assertEqual(detail['size'], 3)
        self.assertEqual({p['id'] for p in detail['top']}, {1, 2, 3})
        self.assertEqual(sum(m['posts'] for m in detail['monthly']), 3)
        data = server.topic_list(0)
        self.assertEqual(data['topics'][0]['size'], 3)
        self.assertEqual(data['edges'], [])

    def test_tombstone_without_type_hides_existing_article(self):
        with connect(self.path) as c:
            hn_sync.save_articles(c, [{'id': 1, 'deleted': True}])
        self.assertNotIn(1, [p['id'] for p in server.stories(3)['posts']])

    def test_empty_snapshot_and_missing_topic(self):
        with connect(self.path) as c:
            c.execute('DELETE FROM stories')
        self.assertEqual(server.topic(3)['size'], 0)
        with self.assertRaises(HTTPException) as error:
            server.stories(999)
        self.assertEqual(error.exception.status_code, 404)

    def test_health_reports_stale_or_unavailable_database(self):
        self.assertTrue(server.health()['stale'])
        with patch.object(server, 'DB', self.path + '.missing'):
            with self.assertRaises(HTTPException) as error:
                server.health()
        self.assertEqual(error.exception.status_code, 503)

    def test_connection_rolls_back_and_closes_on_failure(self):
        with self.assertRaises(RuntimeError):
            with connect(self.path) as c:
                c.execute("UPDATE topics SET name='Changed'")
                raise RuntimeError('stop')
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute('SELECT 1')
        self.assertEqual(server.topic(3)['name'], 'Current')
