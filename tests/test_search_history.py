import json
from pathlib import Path
import sqlite3
import unittest


class SearchHistoryTests(unittest.TestCase):
    def test_history_survives_corpus_expiry_and_prunes_by_event_date(self):
        with sqlite3.connect(':memory:') as db:
            for file in sorted(Path('migrations/search').glob('*.sql')):
                db.executescript(file.read_text())
            db.executescript(Path('migrations/search/0002_search_history.sql').read_text())
            db.execute("INSERT INTO posts VALUES(1,'Original title','https://example.org',100,5,2)")
            result = json.dumps([{'id': 1, 'title': 'Original title', 'position': 1}])
            sql = '''INSERT INTO searches VALUES(?,?,?,'pattern',NULL,'relevance','v1',0,100,200,5,1,0,0,0,?,NULL)'''
            db.execute(sql, ('old', 10, "Rust's compiler", result))
            db.execute(sql, ('new', 100, 'Rust', result))
            db.execute('DELETE FROM posts WHERE id=1')
            self.assertEqual(json.loads(db.execute("SELECT results_json FROM searches WHERE id='new'").fetchone()[0])[0]['title'], 'Original title')
            db.execute('DELETE FROM searches WHERE created_at < ?', (50,))
            self.assertEqual(db.execute('SELECT id FROM searches').fetchall(), [('new',)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM posts').fetchone()[0], 0)
