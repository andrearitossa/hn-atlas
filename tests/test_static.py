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
            c.execute("CREATE TABLE private_notes(token TEXT, email TEXT)")
            c.execute("INSERT INTO private_notes VALUES('private-token','private@example.com')")
        output, release = self.export()
        self.assertEqual(json.loads((release / 'timelines/3.json').read_text())['0'], server.topic_timeline(3))
        self.assertEqual(json.loads((release / 'timelines/3.json').read_text())['zoom'], server.topic_timeline(3, view='zoom'))
        for days in (30, 90, 365):
            self.assertEqual(json.loads((release / 'timelines/all.json').read_text())[str(days)], server.timeline_overview(days))
            self.assertEqual(json.loads((release / 'timelines/3.json').read_text())[str(days)], server.topic_timeline(3, days))
        overview = json.loads((release / 'topics.json').read_text())
        self.assertNotIn('aliases', overview)
        self.assertEqual(overview, server.topic_list(0))
        detail = json.loads((release / 'topics/3.json').read_text())
        expected = server.topic(3)
        self.assertEqual(detail, expected)
        self.assertEqual(json.loads((release / 'stories/3.json').read_text()), server.stories(3)['posts'])
        for path in output.rglob('*'):
            if path.is_file():
                self.assertNotIn('private-token', path.read_text())
                self.assertNotIn('private@example.com', path.read_text())
                self.assertTrue(path.name == '_redirects' or path.suffix in ('.html', '.js', '.json', '.xml', '.txt', '.css'))

    def test_static_page_seeds_data_and_recent_archive_is_bounded(self):
        output, release = self.export()
        overview = json.loads((release / 'topics.json').read_text())
        posts = json.loads((release / 'stories/3.json').read_text())
        recent = json.loads((release / 'recent/3.json').read_text())
        self.assertEqual(recent, [p for p in posts if p['time'] >= overview['as_of'] - 30 * catalog.DAY])
        html = (output / 'topic/current/index.html').read_text()
        initial = json.loads(re.search(r'id="topic-initial" type="application/json">(.*?)</script>', html)[1])
        self.assertEqual(initial['id'], 3)
        self.assertFalse(any('d3' in src for src in re.findall(r'<script[^>]+src="([^"]+)"', html) if not src.endswith('topic.js')))
        self.assertNotIn('data.js', html)
        self.assertIn('id="story-results"><div class="post">', html)
        self.assertIn('id="zt-periods"><section', html)
        self.assertIn('id="activity-chart"', html)
        archive = [p for path in (release / 'archive/3').glob('*.json') for p in json.loads(path.read_text())]
        self.assertEqual(sorted(p['id'] for p in archive), sorted(p['id'] for p in posts))
        for level in ('month', 'week'):
            rows = json.loads((release / f'history/3-{level}.json').read_text())
            self.assertTrue(all(len(p['posts']) <= 3 for p in rows))
            embedded = re.search(fr'<template id="zt-{level}">(.*?)</template>', html, re.S)[1]
            self.assertEqual(embedded.count('class="zt-period"'), len(rows))
            for period in rows:
                self.assertIn(f'data-time="{period["start"]}"', embedded)
                for story in period['posts']:
                    self.assertIn(f'item?id={story["id"]}', embedded)
        import static_pages
        detail = json.loads((release / 'topics/3.json').read_text())
        malicious = {**detail, 'name': '</script><script>alert(1)</script>'}
        history = json.loads((release / 'timelines/3.json').read_text())['zoom']
        html = static_pages.render(Path('index.html').read_text(), overview, malicious, history, posts, 'test')
        self.assertNotIn('</script><script>alert(1)', html)

    def test_snapshot_rebuild_and_packaging_use_only_public_runtime_assets(self):
        output, release = self.export()
        detail_file = release / 'topics/3.json'
        detail = json.loads(detail_file.read_text())
        expected_picks = detail['trending']
        self.assertTrue(expected_picks)
        detail['trending'] = []
        detail_file.write_text(json.dumps(detail))
        with patch.object(publish, 'connect', side_effect=AssertionError('UI rebuild must not query database')):
            publish.rebuild(output, output)
        bundle = Path(self.directory.name) / 'pages'
        prepare(output, bundle)
        current = next((bundle / 'releases').iterdir())
        self.assertEqual(json.loads((current / 'topics/3.json').read_text())['trending'], expected_picks)
        self.assertTrue((current / 'topic.js').is_file())
        self.assertTrue((current / 'site.css').is_file())
        self.assertFalse((current / 'public').exists())
        self.assertFalse((current / 'stories').exists())
        self.assertFalse((current / 'timelines').exists())
        self.assertTrue((current / 'archive/3').is_dir())
        self.assertIn('immutable', (bundle / '_headers').read_text())
        previous = (bundle / 'index.html').read_bytes()
        (output / 'index.html').write_text((output / 'index.html').read_text().replace('data.js', 'missing.js'))
        with self.assertRaisesRegex(ValueError, 'Missing public asset'):
            prepare(output, bundle)
        self.assertEqual((bundle / 'index.html').read_bytes(), previous)

    def test_signup_placement_is_identical_in_preview_and_deploy_bundle(self):
        output, _ = self.export()
        bundle = Path(self.directory.name) / 'pages'
        prepare(output, bundle)
        preview = (output / 'topic/current/index.html').read_text()
        deployed = (bundle / 'topic/current/index.html').read_text()
        for feature in ('newsletter', 'feedback'):
            preview = preview.replace(f'name="hn-{feature}" content=""', f'name="hn-{feature}" content="cloudflare"')
        self.assertEqual(preview, deployed)
        self.assertNotIn('newsletter-cta', deployed)
        self.assertIn('class="topic-follow-link" href="#topic-updates"', deployed)
        self.assertIn('<details class="topic-follow" id="topic-updates">', deployed)
        self.assertLess(deployed.index('<section id="topic-recent"'), deployed.index('<details class="topic-follow"'))
        self.assertLess(deployed.index('<details class="topic-follow"'), deployed.index('<section id="topic-archive"'))
        self.assertEqual(deployed.count('id="follow-form"'), 1)
        self.assertNotIn('id="signup"', deployed)
        self.assertNotIn('id="topic-newsletter"', deployed)
        self.assertIn('Send me weekly highlights', deployed)

    def test_conversation_selects_top_five_then_displays_newest_first(self):
        import static_pages
        _, release = self.export()
        overview = json.loads((release / 'topics.json').read_text())
        detail = json.loads((release / 'topics/3.json').read_text())
        history = json.loads((release / 'timelines/3.json').read_text())['zoom']
        detail['trending'] = [dict(id=i, title=f'Ranked story {i}', url=f'https://example.test/{i}',
                                  time=overview['as_of'] - (7-i)*86400, score=100, descendants=0)
                              for i in range(1,7)]
        html = static_pages.render(Path('index.html').read_text(), overview, detail, history, [], 'test')
        section = re.search(r'<section id="topic-recent".*?</section>', html, re.S)[0]
        self.assertEqual(re.findall(r'Ranked story (\d+)', section), ['5','4','3','2','1'])

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

    def test_topic_html_is_crawlable_and_bundle_retains_metadata(self):
        output, release = self.export()
        detail = json.loads((release / 'topics/3.json').read_text())
        html = (output / 'topic/current/index.html').read_text()
        self.assertIn(f'<title>{detail["name"]} · HN Atlas</title>', html)
        self.assertIn('<link rel="canonical" href="https://hackeratlas.com/topic/current/">', html)
        self.assertIn('property="og:description"', html)
        self.assertIn(f'<h1>{detail["name"]}</h1>', html)
        self.assertIn('https://news.ycombinator.com/item?id=', html)
        self.assertIn('href="/topic/current/"', (output / 'index.html').read_text())
        self.assertIn('https://hackeratlas.com/topic/current/', (output / 'sitemap.xml').read_text())
        self.assertNotIn('/topic/1/', (output / 'sitemap.xml').read_text())
        script = re.search(r'src="([^" ]+topic.js)"', html)[1]
        self.assertTrue((output / 'topic/current' / script).is_file())
        bundle = Path(self.directory.name) / 'pages'
        prepare(output, bundle)
        page = (bundle / 'topic/current/index.html').read_text()
        self.assertIn('name="hn-newsletter" content="cloudflare"', page)
        self.assertIn('<link rel="canonical" href="https://hackeratlas.com/topic/current/">', page)
        self.assertTrue((bundle / '404.html').is_file())
        self.assertIn('/topic/3/ /topic/current/ 301', (bundle / '_redirects').read_text())
        self.assertIn('http-equiv="refresh"', (output / 'topic/3/index.html').read_text())
        self.assertNotIn('/topic/3/', (output / 'sitemap.xml').read_text())

    def test_metadata_escapes_topic_text(self):
        import seo
        html = seo.render('<title>HN Atlas</title><main id="app"></main>',
                          topic={'id': 3, 'slug': 'current', 'name': '<script>&"', 'description': '" onload="bad'})
        self.assertNotIn('<script>', html)
        self.assertIn('&quot; onload=&quot;bad', html)

    def test_live_topic_page_and_current_numeric_route(self):
        response = server.topic_page('current')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'https://hackeratlas.com/topic/current/', response.body)
        self.assertIn(b'src="/data.js"', response.body)
        self.assertEqual(server.topic_page(3).status_code, 301)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            server.topic_page(1)
        self.assertEqual(raised.exception.status_code, 404)

    def test_slugs_handle_punctuation_unicode_duplicates_and_numeric_names(self):
        import topics
        with connect(self.path) as c:
            c.execute('DELETE FROM topics')
            names = ['LLM Advances', 'Café & Tools', 'Same!', 'Same?', 'Same-3', '1234', '日本語']
            c.executemany('INSERT INTO topics(id,name) VALUES(?,?)', enumerate(names, 1))
            slugs = topics.slugs(c)
            self.assertEqual(slugs[1], 'llm-advances')
            self.assertEqual(slugs[2], 'cafe-tools')
            self.assertEqual(len(set(slugs.values())), len(names))
            self.assertTrue(all(re.fullmatch('[a-z0-9-]+', slug) and not slug.isdigit()
                                for slug in slugs.values()))
            self.assertEqual(slugs, topics.slugs(c))

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
                         ['/api/newsletter/*', '/api/feedback', '/api/search/*'])

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
        cases = [{'query': q, 'expected': server.stories(3, **q)} for q in queries]
        expectations = Path(self.directory.name) / 'expected.json'
        expectations.write_text(json.dumps(cases))
        subprocess.run(['node', 'tests/check_static.cjs', str(output), str(expectations)], check=True)
