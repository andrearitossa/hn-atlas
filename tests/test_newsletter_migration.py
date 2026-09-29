"""The shared newsletter migration preserves subscriptions and send history."""
import json
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NewsletterMigrationTests(unittest.TestCase):
    def test_legacy_duplicates_cancellations_and_delivery_history(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        for migration in ('0001_newsletter.sql', '0003_newsletter_delivery.sql'):
            conn.executescript((ROOT / 'migrations' / migration).read_text())
        conn.executemany('INSERT INTO newsletter_signups VALUES (?,?,?)', [
            ('site@example.com', 7, 'Programming'),
            ('TWO@example.com', 7, 'Programming'),
        ])
        conn.executemany('INSERT INTO newsletter_test_subscriptions VALUES (?,?,?,?,?,?,?)', [
            ('old-a', 'ONE@example.com', 7, 'Programming', 1, 0, 0),
            ('old-b', 'one@example.com', 7, 'Programming', 2, 0, 1),
            ('old-c', 'two@example.com', 7, 'Programming', 3, 0, 0),
        ])
        conn.executemany('''INSERT INTO newsletter_test_issues
            (subscription,edition,due_at,prepared_at,source_as_of,subject,body,posts,html,state,message_id,sent_at,error)
            VALUES (?,10,10,10,9,'Subject','Body',?,'<p>Content</p>',?,?,10,NULL)''', [
            ('old-a', '[{"id":1}]', 'sent', 'message-a'),
            ('old-b', '[{"id":1},{"id":2}]', 'sending', None),
            ('old-c', '[{"id":3}]', 'sent', 'message-c'),
        ])
        conn.executemany('''INSERT INTO newsletter_test_issues
            (subscription,edition,due_at,prepared_at,source_as_of,subject,body,posts,html,state)
            VALUES (?,11,11,11,10,'Next','Body','[]','<p>Next</p>',?)''', [
            ('old-b', 'sending'), ('old-c', 'failed'),
        ])
        conn.execute('''INSERT INTO newsletter_test_issues
            (subscription,edition,due_at,prepared_at,source_as_of,subject,body,posts,state)
            VALUES ('old-c',12,12,12,11,'Third','<script>unsafe</script>','[]','ready')''')
        migration = (ROOT / 'migrations/0004_shared_newsletters.sql').read_text()
        conn.executescript(migration)

        rows = conn.execute('SELECT id,email,unsubscribed_at,legacy_tokens FROM newsletter_subscriptions').fetchall()
        self.assertEqual(len(rows), 3)
        one = next(row for row in rows if row[1] == 'one@example.com')
        self.assertEqual(one[0], 'old-a')
        self.assertIsNotNone(one[2])
        self.assertEqual(set(json.loads(one[3])), {'old-a', 'old-b'})
        site = next(row for row in rows if row[1] == 'site@example.com')
        self.assertIsNone(site[2])
        self.assertEqual(json.loads(site[3]), [])
        self.assertEqual(conn.execute('SELECT count(*) FROM newsletter_issues').fetchone()[0], 3)
        self.assertIn('&lt;script&gt;', conn.execute('SELECT html FROM newsletter_issues WHERE edition=12').fetchone()[0])
        history = json.loads(conn.execute('SELECT posts FROM newsletter_issues WHERE edition=10').fetchone()[0])
        self.assertEqual({post['id'] for post in history}, {1, 2, 3})
        self.assertEqual(conn.execute('SELECT sent_count FROM newsletter_issues WHERE edition=10').fetchone()[0], 2)
        self.assertEqual(conn.execute('SELECT sent_count FROM newsletter_issues WHERE edition=11').fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT count(*) FROM newsletter_deliveries WHERE state='sent'").fetchone()[0], 2)
        self.assertEqual(set(conn.execute('SELECT state FROM newsletter_deliveries WHERE edition=11').fetchall()),
                         {('sending',), ('failed',)})
        old_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'newsletter_signups', 'newsletter_test_subscriptions',
                         'newsletter_test_issues', 'newsletter_test_checks'}.isdisjoint(old_tables))


if __name__ == '__main__':
    unittest.main()
