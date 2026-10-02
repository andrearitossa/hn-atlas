import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from database import connect
import publish
import feed_sync
from scripts.prepare_pages import prepare
from scripts.migrate_feed_profiles import maintenance
import test_browsing


class FeedExportTests(unittest.TestCase):
    setUp = test_browsing.BrowsingTests.setUp

    def test_shared_export_and_snapshot_rebuild_have_memberships_no_private_data(self):
        with connect(self.path) as c:
            c.execute('INSERT INTO stories(id,title,url,time,score,fetched_at) VALUES(100,?,?,?,?,?)',
                      ('Shared story', 'https://example.com/shared', self.clock, 80, self.clock))
            c.execute('INSERT INTO story_topics(id,topic) VALUES(100,3)')
        out = Path(self.directory.name) / 'feed-site'
        publish.publish(self.path, out)
        first = next((out / 'releases').iterdir())
        self.assertFalse((first / 'feed.json').exists())
        data = json.loads(feed_sync.payload(out))
        self.assertEqual(data['version'], 1)
        self.assertEqual(data['edition'], first.name)
        self.assertEqual(len({p['id'] for p in data['posts']}), len(data['posts']))
        self.assertTrue(all(data['as_of'] - 30*86400 <= p['time'] <= data['as_of'] for p in data['posts']))
        self.assertTrue(all(p['topics'] and p['article_key'] for p in data['posts']))
        self.assertLessEqual(len(feed_sync.payload(out).encode()), 1500000)
        self.assertNotIn('email', data)
        with patch.object(publish, 'connect', side_effect=AssertionError('No database on rebuild')):
            publish.rebuild(out, out)
        bundle = Path(self.directory.name) / 'feed-pages'
        prepare(out, bundle)
        current = next((bundle / 'releases').iterdir())
        self.assertFalse((current / 'feed.json').exists())
        rebuilt = json.loads(feed_sync.payload(out))
        self.assertEqual(rebuilt['posts'], data['posts'])
        self.assertEqual(rebuilt['edition'], current.name)
        html = (bundle / 'for-you/index.html').read_text()
        self.assertIn(f'/releases/{current.name}/feed.js', html)
        self.assertIn("You're caught up", html)
        self.assertIn('Continue to my feed', html)
        self.assertNotIn('Saved', html)
        self.assertFalse((current / 'feed-ranking.js').exists())
        self.assertIn('/api/reader/*', json.loads((bundle / '_routes.json').read_text())['include'])


    def test_private_catalog_and_legacy_history_survive_replacement(self):
        out = Path(self.directory.name) / 'feed-sync'
        publish.publish(self.path, out)
        value = feed_sync.payload(out)
        data = json.loads(value)
        post = data['posts'][0]
        self.assertEqual(len(post['article_key']), 64)
        conn = sqlite3.connect(':memory:')
        maintenance(conn.executescript, self.clock)
        conn.execute("INSERT INTO feed_users(id,email,created_at,verified_at) VALUES('u','a@example.com',?,?)", (self.clock, self.clock))
        conn.execute("INSERT INTO feed_visits VALUES('v','u','old','v','[3]','[]',?)", (self.clock,))
        conn.execute("INSERT INTO feed_events VALUES('u','e','v','article_opened',?,1,?)", (post['id'], self.clock))
        conn.execute(feed_sync.UPSERT, (value,))
        conn.execute(feed_sync.BACKFILL)
        conn.execute(feed_sync.BACKFILL)
        self.assertEqual(conn.execute('SELECT article_key,seen_at,opened_at FROM feed_story_state').fetchall(), [(post['article_key'], self.clock, self.clock)])
        data['edition'] = 'b' * 32
        conn.execute(feed_sync.UPSERT, (json.dumps(data),))
        self.assertEqual(conn.execute('SELECT count(*) FROM feed_story_state').fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(feed_sync.UPSERT, ('broken JSON',))
        self.assertEqual(json.loads(conn.execute('SELECT payload FROM feed_catalog').fetchone()[0])['edition'], 'b' * 32)
        conn.close()


class FeedMaintenanceTests(unittest.TestCase):
    def test_retention_is_idempotent_and_keeps_preferences(self):
        conn = sqlite3.connect(':memory:')
        conn.execute('PRAGMA foreign_keys=ON')
        now = 1800000000
        maintenance(conn.executescript, now)
        conn.execute("INSERT INTO feed_users(id,email,created_at,verified_at) VALUES('u','a@example.com',?,?)", (now, now))
        conn.execute("UPDATE feed_users SET topics_json='[3]' WHERE id='u'")
        for ident, stamp in [('old', now-91*86400), ('new',now)]:
            conn.execute("INSERT INTO feed_visits VALUES(?,'u','edition','v','[3]','[123]',?)", (ident,stamp))
            conn.execute("INSERT INTO feed_events VALUES('u',?,?,'visible',123,1,?)", (ident,ident,stamp))
        maintenance(conn.executescript, now)
        maintenance(conn.executescript, now)
        self.assertEqual(conn.execute('SELECT id FROM feed_visits').fetchall(), [('new',)])
        self.assertEqual(conn.execute('SELECT event_id FROM feed_events').fetchall(), [('new',)])
        self.assertEqual(conn.execute('SELECT topic_id FROM feed_user_topics').fetchall(), [(3,)])
        conn.close()
