import fcntl
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import weekly_newsletters as pipeline

SCHEMA = Path(__file__).resolve().parents[1] / 'workers/newsletter-test/schema.sql'

def stamp(value):
    return int(datetime.fromisoformat(value).timestamp())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.db = self.path/'atlas.db'
        self.now = stamp('2026-09-28T08:00:00+00:00')
        local = sqlite3.connect(self.db)
        local.executescript("""CREATE TABLE maintenance(key TEXT PRIMARY KEY,value INTEGER);
            CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT);
            CREATE TABLE topic_registry(id INTEGER PRIMARY KEY,status TEXT);
            INSERT INTO topics VALUES(1185,'Robotics');
            INSERT INTO topic_registry VALUES(1185,'active');""")
        local.execute("INSERT INTO maintenance VALUES('refresh',?)", (self.now,))
        local.commit(); local.close()
        self.remote = sqlite3.connect(':memory:')
        self.addCleanup(self.remote.close)
        self.remote.row_factory = sqlite3.Row
        self.remote.executescript(SCHEMA.read_text())
        self.remote.executemany('INSERT INTO newsletter_subscriptions(id,email,topic,created_at) VALUES(?,?,1185,0)',
                               [('one','one@example.com'),('two','two@example.com')])
        self.remote.commit()
        overview = patch.object(pipeline.newsletter, 'overview', side_effect=lambda p: dict(p, summary='A clear overview.'))
        overview.start(); self.addCleanup(overview.stop)
        choose = patch.object(pipeline.newsletter.llm, 'ask_json', return_value={'ids':[1]})
        choose.start(); self.addCleanup(choose.stop)
        self.queries = []
        for name, value in [('DB',self.db),('query',self.query)]:
            patcher = patch.object(pipeline, name, value)
            patcher.start(); self.addCleanup(patcher.stop)

    def query(self, sql, params=()):
        self.queries.append(sql)
        cursor = self.remote.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
        self.remote.commit()
        return rows

    def candidates(self):
        return [dict(id=1,title='Robotics breakthrough',url='https://example.com/story',
                     time=pipeline.edition_at(self.now)-100,score=50,descendants=12)]

    def test_two_subscribers_share_one_immutable_issue_and_worker_owns_count(self):
        with patch.object(pipeline.newsletter, 'candidates', return_value=self.candidates()) as posts, \
             patch.object(pipeline.newsletter, 'render_html', wraps=pipeline.newsletter.render_html) as render:
            edition = pipeline.prepare(self.now)
            posts.assert_called_once()
            render.assert_called_once()
            row = self.remote.execute('SELECT * FROM newsletter_issues').fetchone()
            self.assertEqual(row['sent_count'], 0)
            self.assertEqual(row['edition'], edition)
            self.assertIn('Robotics breakthrough', row['html'])
            self.remote.execute('UPDATE newsletter_issues SET sent_count=2')
            self.remote.commit()
            pipeline.prepare(self.now)
            self.assertEqual(posts.call_count, 1)
            self.assertEqual(render.call_count, 1)
            row2 = self.remote.execute('SELECT * FROM newsletter_issues').fetchone()
            self.assertEqual(row2['html'], row['html'])
            self.assertEqual(row2['sent_count'], 2)
            self.assertEqual(self.remote.execute('SELECT count(*) FROM newsletter_issues').fetchone()[0], 1)

    def test_preview_uses_same_html_without_writing_remote_or_sending(self):
        with patch.object(pipeline.newsletter, 'candidates', return_value=self.candidates()), patch('requests.post') as send:
            output = self.path/'preview'
            pipeline.prepare(self.now, output)
            self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql in self.queries))
            self.assertEqual(self.remote.execute('SELECT count(*) FROM newsletter_issues').fetchone()[0], 0)
            pipeline.prepare(self.now)
            stored = self.remote.execute('SELECT html FROM newsletter_issues').fetchone()['html']
            self.assertEqual((output/'topic-1185.html').read_text(),
                             stored.replace('{{unsubscribe_url}}','https://example.invalid/unsubscribe/preview'))
            self.assertFalse(list(output.glob('*.txt')))
            send.assert_not_called()

    def test_stale_checkpoint_stops_before_remote_access(self):
        local = sqlite3.connect(self.db)
        local.execute('UPDATE maintenance SET value=?', (self.now-37*3600,))
        local.commit(); local.close()
        with self.assertRaisesRegex(RuntimeError, 'refresh checkpoint'):
            pipeline.prepare(self.now)
        self.assertEqual(self.queries, [])

    def test_prior_issue_history_is_not_used_for_selection(self):
        post = self.candidates()[0]
        self.remote.execute('INSERT INTO newsletter_issues(topic,edition,prepared_at,source_as_of,subject,html,posts,sent_count) VALUES(1185,0,0,0,?,?,?,1)',
                            ('Old issue','<p>Old issue</p>',json.dumps([post])))
        self.remote.commit()
        with patch.object(pipeline.newsletter, 'candidates', return_value=[post]):
            pipeline.prepare(self.now)
        self.assertEqual(self.remote.execute('SELECT count(*) FROM newsletter_issues').fetchone()[0], 2)
        self.assertFalse(any('SELECT posts' in q for q in self.queries))

    def test_monday_catchup_and_dst_keep_the_due_sunday_edition(self):
        cases = [('2026-09-28T08:00:00+00:00','2026-09-27T16:00:00+00:00'),
                 ('2026-09-27T15:59:00+00:00','2026-09-20T16:00:00+00:00'),
                 ('2026-03-30T08:00:00+00:00','2026-03-29T16:00:00+00:00'),
                 ('2026-10-26T08:00:00+00:00','2026-10-25T17:00:00+00:00')]
        for run, due in cases:
            with self.subTest(run=run):
                self.assertEqual(pipeline.edition_at(stamp(run)), stamp(due))

    def test_preparation_precedes_send_and_errors_never_trigger_send(self):
        response = Mock()
        response.json.return_value = dict(sent=2,failures=0,pending=0)
        events = []
        def prepare(now):
            events.append('prepare')
            return 123
        def send(*args, **kwargs):
            events.append('send')
            self.assertEqual(kwargs['json'], {'edition':123})
            return response
        with patch.dict('os.environ', {'NEWSLETTER_ADMIN_TOKEN':'test'}), \
             patch.object(pipeline,'prepare',side_effect=prepare), patch('requests.post',side_effect=send):
            pipeline.run_weekly(self.now)
            self.assertEqual(events, ['prepare','send'])
        with patch.dict('os.environ', {'NEWSLETTER_ADMIN_TOKEN':'test'}), \
             patch.object(pipeline,'prepare',side_effect=RuntimeError('stale')), patch('requests.post') as send:
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                pipeline.run_weekly(self.now)
            send.assert_not_called()

    def test_waits_for_previous_work_then_prepares(self):
        started, prepared = threading.Event(), threading.Event()
        errors = []
        response = Mock()
        response.json.return_value = dict(sent=0,failures=0,pending=0)
        def run():
            started.set()
            try:
                pipeline.run_weekly(self.now)
            except BaseException as error:
                errors.append(error)
        with open(str(self.db)+'.worker.lock','w') as held, \
             patch.dict('os.environ', {'NEWSLETTER_ADMIN_TOKEN':'test'}), \
             patch.object(pipeline,'prepare',side_effect=lambda now: prepared.set() or 123), \
             patch('requests.post',return_value=response):
            fcntl.flock(held, fcntl.LOCK_EX)
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(prepared.wait(.05))
            fcntl.flock(held, fcntl.LOCK_UN)
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(prepared.is_set())
            self.assertEqual(errors, [])

    def test_disabled_or_incomplete_delivery_fails_the_workflow(self):
        with patch.dict('os.environ', {'NEWSLETTER_ADMIN_TOKEN':'test'}), \
             patch.object(pipeline,'prepare',return_value=123), patch('requests.post') as send:
            for result in [dict(disabled=True,sent=0,failures=0,pending=0),
                           dict(sent=1,failures=1,pending=1),dict(sent=1,failures=0,pending=1),{}]:
                with self.subTest(result=result):
                    send.return_value.json.return_value = result
                    with self.assertRaisesRegex(RuntimeError, 'did not complete'):
                        pipeline.run_weekly(self.now)

    def test_fresh_corpus_does_not_trigger_another_refresh(self):
        with patch('refresh.refresh') as refresh:
            pipeline.ensure_fresh(self.now)
            refresh.assert_not_called()

    def test_stale_corpus_refreshes_before_preparation_and_delivery(self):
        with sqlite3.connect(self.db) as local:
            local.execute('UPDATE maintenance SET value=?', (self.now-37*3600,))
        events = []
        def refresh(conn, now, discover_topics):
            self.assertFalse(discover_topics)
            events.append('refresh')
            conn.execute("UPDATE maintenance SET value=? WHERE key='refresh'", (now,))
            conn.commit()
        def prepare(now):
            with sqlite3.connect(self.db) as local:
                self.assertEqual(local.execute('SELECT value FROM maintenance').fetchone()[0], now)
            events.append('prepare')
            return 123
        response = Mock()
        response.json.return_value = dict(sent=0, failures=0, pending=0)
        with patch.dict('os.environ', {'NEWSLETTER_ADMIN_TOKEN':'test'}), \
             patch('refresh.refresh', side_effect=refresh), \
             patch.object(pipeline, 'prepare', side_effect=prepare), \
             patch('requests.post', side_effect=lambda *a, **kw: events.append('send') or response):
            pipeline.run_weekly(self.now)
        self.assertEqual(events, ['refresh', 'prepare', 'send'])

    def test_failed_catchup_never_prepares_or_sends(self):
        with sqlite3.connect(self.db) as local:
            local.execute('DELETE FROM maintenance')
        with patch.dict('os.environ', {'NEWSLETTER_ADMIN_TOKEN':'test'}), \
             patch('refresh.refresh', side_effect=RuntimeError('offline')), \
             patch.object(pipeline, 'prepare') as prepare, patch('requests.post') as send:
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                pipeline.run_weekly(self.now)
            prepare.assert_not_called()
            send.assert_not_called()


class CloudflareAuthTests(unittest.TestCase):
    def test_oauth_is_obtained_through_wrangler_for_unattended_refresh(self):
        response = Mock(ok=True)
        response.json.return_value = {'success':True,'result':[{'success':True,'results':[]}]}
        with patch.dict('os.environ', {'CLOUDFLARE_API_TOKEN':''}), \
             patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=0,stdout='{"token":"private"}')) as auth, \
             patch('requests.post', return_value=response) as request:
            self.assertEqual(pipeline.query('SELECT 1'), [])
            self.assertEqual(auth.call_args.args[0][-3:], ['auth','token','--json'])
            self.assertTrue(auth.call_args.kwargs['capture_output'])
            self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer private')

    def test_failed_auth_does_not_expose_cli_output_or_query_d1(self):
        with patch.dict('os.environ', {'CLOUDFLARE_API_TOKEN':''}), \
             patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=1,stdout='secret',stderr='secret')), \
             patch('requests.post') as request:
            with self.assertRaisesRegex(RuntimeError, '^Cloudflare authentication failed;') as caught:
                pipeline.query('SELECT 1')
            self.assertNotIn('secret', str(caught.exception))
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
