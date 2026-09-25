import sqlite3
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import requests

import hn_sync
import pages
import production
import server
from database import connect


class RuntimeTests(unittest.TestCase):
    def test_sync_failure_does_not_advance_high_water_mark(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = hn_sync.open_db(f'{directory}/hn.db')
            hn_sync.set_state(conn, 'high', 100)
            conn.commit()
            with patch.object(hn_sync, 'get_json', side_effect=lambda path: 102 if path == 'maxitem' else None), \
                 patch.object(hn_sync, 'fetch_item', side_effect=requests.ConnectionError('offline')):
                with self.assertRaises(requests.ConnectionError):
                    with __import__('concurrent.futures').futures.ThreadPoolExecutor(2) as pool:
                        hn_sync.catch_up(conn, pool, chunk=2)
            self.assertEqual(hn_sync.get_state(conn, 'high'), 100)
            conn.close()

    def test_signup_attempts_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(f'{directory}/hn.db')
            conn.executescript(production.SCHEMA)
            for _ in range(3):
                self.assertTrue(production.allow_signup(conn, '198.51.100.8', 'a@example.com'))
            self.assertFalse(production.allow_signup(conn, '198.51.100.8', 'a@example.com'))
            self.assertTrue(production.allow_signup(conn, '198.51.100.8', 'b@example.com'))
            conn.close()

    def test_page_fetch_rejects_private_destination_and_unsafe_scheme(self):
        with patch.object(pages.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(ValueError):
                pages.public_url('http://example.com/')
        with self.assertRaises(ValueError):
            pages.public_url('file:///etc/passwd')

    def test_centroid_classification_preserves_registry_id(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(f'{directory}/hn.db')
            production.setup(conn)
            mean, ids, centers = production.model(conn)
            label = production.classify(conn, [42], np.array([mean + centers[7]]))[0]
            self.assertEqual(label[:2], (42, int(ids[7])))
            conn.close()

    def test_subscription_confirm_and_unsubscribe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f'{directory}/hn.db'
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT)')
            conn.execute("INSERT INTO topics VALUES(0,'Test topic')")
            production.setup(conn)
            conn.close()
            request = server.SubscriptionRequest(email='reader@example.com', topic=0)
            client = SimpleNamespace(client=SimpleNamespace(host='198.51.100.8'))
            env = {'SMTP_HOST': 'smtp.example.com', 'SMTP_FROM': 'news@example.com',
                   'PUBLIC_URL': 'https://example.com'}
            with patch.object(server, 'DB', path), patch.dict('os.environ', env), \
                 patch.object(production, 'send') as send:
                self.assertEqual(server.subscribe(request, client)['status'], 'confirmation_sent')
                def subscription_value(sql):
                    with connect(path, readonly=True) as c:
                        return c.execute(sql).fetchone()[0]

                token = subscription_value('SELECT token FROM subscriptions')
                self.assertIn(token, send.call_args.args[2])
                self.assertEqual(subscription_value('SELECT confirmed FROM subscriptions'), 0)
                self.assertEqual(server.confirm(token).status_code, 200)
                self.assertEqual(subscription_value('SELECT confirmed FROM subscriptions'), 0)
                self.assertEqual(server.confirm_post(token).status_code, 200)
                self.assertEqual(server.unsubscribe(token).status_code, 200)
                self.assertEqual(subscription_value('SELECT count(*) FROM subscriptions'), 1)
                self.assertEqual(server.unsubscribe_post(token).status_code, 200)

    def test_weekly_does_not_merge_topics_or_move_subscriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(f'{directory}/hn.db')
            conn.executescript('''CREATE TABLE stories(id INTEGER PRIMARY KEY,title TEXT,score INTEGER,
                time INTEGER,dead INTEGER,deleted INTEGER,url TEXT,text TEXT);
                CREATE TABLE embeddings(id INTEGER PRIMARY KEY,vec BLOB);
                CREATE TABLE story_topics(id INTEGER PRIMARY KEY,topic INTEGER,sim REAL,margin REAL);
                CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT,description TEXT,size INTEGER,
                    cohesion REAL,x REAL,y REAL);''')
            conn.executescript(production.SCHEMA)
            now = int(time.time())
            base = np.load('data/topic_model.npz')
            center = base['centroids'][0].astype('<f4')
            raw = (base['mean'] + center).astype('<f4').tobytes()
            for topic in (0, 1):
                conn.execute('INSERT INTO topics VALUES (?,?,?,?,?,?,?)',
                             (topic, f'Topic {topic}', '', 0, 0, .5, .5))
                conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                             (topic, center.tobytes(), now - 86400))
            for item_id in range(90):
                topic = 0 if item_id < 40 else 1
                conn.execute("INSERT INTO stories VALUES (?,?,?,?,0,0,'','')",
                             (item_id, 'Post', 10, now - 3600))
                conn.execute('INSERT INTO embeddings VALUES (?,?)', (item_id, raw))
                conn.execute('INSERT INTO story_topics VALUES (?,?,.9,0)', (item_id, topic))
            conn.execute("INSERT INTO maintenance VALUES ('weekly',?)", (now - 8 * 86400,))
            conn.execute("INSERT INTO subscriptions VALUES ('token','reader@example.com',0,'weekly',1,?,0)", (now,))
            conn.commit()
            events = production.weekly(conn, now)
            self.assertEqual(events, [])
            self.assertEqual(conn.execute('SELECT topic FROM subscriptions').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM story_topics WHERE topic=1').fetchone()[0], 50)
            conn.close()

    def test_digest_delivery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(f'{directory}/hn.db')
            conn.executescript('''CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT);
                CREATE TABLE stories(id INTEGER PRIMARY KEY,title TEXT,url TEXT,score INTEGER,
                    descendants INTEGER,time INTEGER,dead INTEGER,deleted INTEGER);
                CREATE TABLE story_topics(id INTEGER PRIMARY KEY,topic INTEGER,sim REAL);''')
            conn.executescript(production.SCHEMA)
            now = int(time.time())
            conn.execute("INSERT INTO topics VALUES(0,'Test topic')")
            conn.execute('INSERT INTO stories VALUES (1,\'Useful link\',\'https://example.com\',50,10,?,0,0)',
                         (now - 3600,))
            conn.execute('INSERT INTO story_topics VALUES (1,0,.7)')
            conn.execute("INSERT INTO subscriptions VALUES ('token','reader@example.com',0,'weekly',1,?,0)", (now,))
            conn.commit()
            with patch.object(production, 'send') as send:
                self.assertEqual(production.deliver(conn, 'https://example.com'), 1)
                self.assertEqual(production.deliver(conn, 'https://example.com'), 0)
                self.assertEqual(send.call_count, 1)
                self.assertIn('unsubscribe/token', send.call_args.args[3])
            conn.close()


if __name__ == '__main__':
    unittest.main()
