import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import unittest
import time
from unittest.mock import patch

import catalog
from database import connect
import publish
import server
import test_browsing
from scripts.prepare_pages import prepare


class StaticTests(unittest.TestCase):
    setUp = test_browsing.BrowsingTests.setUp

    def export(self):
        output = Path(self.directory.name) / 'site'
        index = publish.publish(self.path, output)
        root = re.search(r'name="hn-data" content="\./([^"]+)"', index.read_text())[1]
        return output, output / root

    def test_public_export_matches_api_and_excludes_private_data(self):
        with connect(self.path) as c:
            c.execute("INSERT INTO subscriptions VALUES('private-token','private@example.com',3,'weekly',1,0,0)")
        output, release = self.export()
        self.assertEqual(json.loads((release / 'timelines/3.json').read_text())['0'], server.topic_timeline(1))
        self.assertEqual(json.loads((release / 'timelines/3.json').read_text())['zoom'], server.topic_timeline(1, view='zoom'))
        for days in (30, 90, 365):
            self.assertEqual(json.loads((release / 'timelines/all.json').read_text())[str(days)], server.timeline_overview(days))
            self.assertEqual(json.loads((release / 'timelines/3.json').read_text())[str(days)], server.topic_timeline(1, days))
        overview = json.loads((release / 'topics.json').read_text())
        self.assertEqual(overview.pop('aliases'), {'1': 3, '2': 3})
        self.assertEqual(overview, server.topic_list(0))
        detail = json.loads((release / 'topics/3.json').read_text())
        digests = detail.pop('digests')
        expected = server.topic(3)
        expected['newsletter_available'] = False
        self.assertEqual(detail, expected)
        for cadence in ('weekly', 'monthly'):
            self.assertEqual(digests[cadence], server.digest(3, cadence))
        self.assertEqual(json.loads((release / 'stories/3.json').read_text()), server.stories(3)['posts'])
        for path in output.rglob('*'):
            if path.is_file():
                self.assertNotIn('private-token', path.read_text())
                self.assertNotIn('private@example.com', path.read_text())
                self.assertIn(path.suffix, ('.html', '.js', '.json'))

    def test_failed_build_preserves_previous_site_and_success_keeps_old_release(self):
        output, old_release = self.export()
        old_index = (output / 'index.html').read_bytes()
        with patch.object(catalog, 'detail', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.export()
        self.assertEqual((output / 'index.html').read_bytes(), old_index)
        self.assertFalse(list((output / 'releases').glob('.build-*')))
        _, new_release = self.export()
        self.assertNotEqual(old_release, new_release)
        self.assertTrue((old_release / 'topics.json').exists())

    def test_export_uses_one_database_snapshot(self):
        with connect(self.path) as c:
            c.execute('PRAGMA journal_mode=WAL')
        original = catalog.detail

        def change_during_export(c, topic_id, as_of=None):
            with connect(self.path) as writer:
                writer.execute("UPDATE stories SET title='Changed during export' WHERE id=1")
            return original(c, topic_id, as_of)

        with patch.object(catalog, 'detail', side_effect=change_during_export):
            _, release = self.export()
        posts = json.loads((release / 'stories/3.json').read_text())
        self.assertEqual(next(p['title'] for p in posts if p['id'] == 1), 'Rust 100% useful')

    def test_missing_database_is_not_created(self):
        missing = Path(self.directory.name) / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError):
            publish.publish(missing, Path(self.directory.name) / 'site')
        self.assertFalse(missing.exists())

    def test_pages_bundle_enables_signup_and_includes_only_current_public_catalog(self):
        output, old_release = self.export()
        output, current_release = self.export()
        bundle = Path(self.directory.name) / 'pages'
        prepare(output, bundle)
        self.assertIn('name="hn-newsletter" content="cloudflare"', (bundle / 'index.html').read_text())
        self.assertIn('name="hn-newsletter" content=""', (output / 'index.html').read_text())
        self.assertFalse((bundle / 'releases' / old_release.name).exists())
        self.assertTrue((bundle / 'releases' / current_release.name).exists())
        overview = json.loads((current_release / 'topics.json').read_text())
        self.assertEqual(json.loads((bundle / 'newsletter-topics.json').read_text()),
                         {str(t['id']): t['name'] for t in overview['topics']})
        self.assertEqual(json.loads((bundle / '_routes.json').read_text())['include'],
                         ['/api/newsletter/*', '/api/feedback'])

    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser data parity checks')
    def test_browser_queries_match_api(self):
        with connect(self.path) as c:
            c.execute('UPDATE stories SET score=NULL,descendants=NULL WHERE id=1')
            c.execute('UPDATE stories SET score=0,descendants=0 WHERE id=2')
        output, release = self.export()
        queries = [{}, {'q': 'rUsT'}, {'q': '%'}, {'q': '_'}, {'q': 'example.org'},
                   {'q': "' OR 1=1 --"}, {'days': 7}, {'days': 30}, {'sort': 'top'},
                   {'sort': 'discussed'}, {'limit': 1}, {'limit': 1, 'offset': 1}, {'offset': 3}, {'year': 2024}, {'year': time.gmtime(self.clock).tm_year},
                   {'year': time.gmtime(self.clock).tm_year, 'days': 30, 'sort': 'top'}]
        cases = [{'query': q, 'expected': server.stories(1, **q)} for q in queries]
        expectations = Path(self.directory.name) / 'expected.json'
        expectations.write_text(json.dumps(cases))
        subprocess.run(['node', 'tests/check_static.cjs', str(output), str(expectations)], check=True)
