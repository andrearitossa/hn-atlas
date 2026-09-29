import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import analytics


class AnalyticsTests(unittest.TestCase):
    def test_utc_boundaries_gaps_and_current_memberships(self):
        monday = 1704067200  # Monday 1 January 2024
        overview = {'as_of': monday + 3*analytics.WEEK, 'topics': [
            {'id': 2, 'name': 'B', 'slug': 'b'}, {'id': 1, 'name': 'A', 'slug': 'a'}]}
        shared = {'id': 1, 'time': monday}
        posts = {1: [shared, {'time': monday-1}, {'time': overview['as_of']+1},
                     {'time': monday, 'deleted': True}],
                 2: [shared, {'time': monday+2*analytics.WEEK}]}
        result = analytics.build(overview, posts.__getitem__)
        self.assertEqual(result['periods']['week']['starts'][0], monday-analytics.WEEK)
        self.assertEqual(result['periods']['week']['counts'], [[1,0],[1,1],[0,0],[0,1],[0,0]])
        self.assertEqual(result['periods']['week']['picks'], [[None,None],[1,1],[None,None],[None,None],[None,None]])
        self.assertEqual([t['id'] for t in result['topics']], [1,2])

    def test_empty_archive(self):
        result = analytics.build({'as_of': 1704067200, 'topics': []}, lambda _: [])
        self.assertEqual(result['periods']['week']['counts'], [[]])
        self.assertEqual(result['periods']['week']['picks'], [[]])
        self.assertEqual(result['stories'], {})

    def test_calendar_aggregation_splits_weeks_at_month_and_year_boundaries(self):
        from datetime import datetime, timezone
        def stamp(date):
            return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())
        dates = ['2023-12-31', '2024-01-01', '2024-01-31', '2024-02-01', '2024-02-29', '2025-01-01']
        data = analytics.build({'as_of': stamp('2025-01-02'), 'topics': [{'id': 1, 'name': 'A', 'slug': 'a'}]},
                               lambda _: [{'time': stamp(date)} for date in dates])
        monthly = data['periods']['month']
        self.assertEqual(monthly['counts'][:4], [[1], [2], [2], [0]])
        self.assertEqual(monthly['ends'][2], stamp('2024-03-01'))
        self.assertEqual(data['periods']['year']['counts'], [[1], [4], [1]])
        for series in data['periods'].values():
            self.assertEqual(sum(row[0] for row in series['counts']), len(dates))

    def test_representative_stories_rank_and_align_with_periods(self):
        monday = 1704067200
        overview = {'as_of': monday + analytics.WEEK, 'topics': [
            {'id': 9, 'name': 'Z', 'slug': 'z', 'description': 'A description'},
            {'id': 4, 'name': 'A', 'slug': 'a'}]}
        def post(ident, seconds, score, **flags):
            return {'id': ident, 'title': f'Story {ident}', 'time': monday + seconds,
                    'score': score, **flags}
        posts = {
            4: [post(1, 10, 10), post(2, 20, 10), post(3, 20, 10),
                post(4, 30, 50, dead=True), post(5, 40, 80, deleted=True),
                post(6, analytics.WEEK + 1, 100), post(7, -1, 3),
                post(8, analytics.WEEK, None)],
            9: [post(3, 20, 10), post(10, 0, 11),
                post(9, analytics.WEEK + 1, 500)]}
        result = analytics.build(overview, posts.__getitem__)
        weekly = result['periods']['week']
        self.assertEqual(weekly['counts'], [[1, 0], [3, 2], [1, 0]])
        self.assertEqual(weekly['picks'], [[7, None], [3, 10], [8, None]])
        self.assertEqual(result['periods']['month']['picks'], [[7, None], [3, 10]])
        self.assertEqual(result['periods']['year']['picks'], [[7, None], [3, 10]])
        self.assertEqual(set(result['stories']), {'3', '7', '8', '10'})
        self.assertEqual(result['stories']['3'], {'id': 3, 'title': 'Story 3', 'score': 10,
                                                  'time': monday + 20})
        self.assertEqual(result['topics'][1]['description'], 'A description')
        for period in result['periods'].values():
            self.assertEqual(len(period['starts']), len(period['ends']))
            self.assertEqual(len(period['starts']), len(period['counts']))
            self.assertEqual(len(period['starts']), len(period['picks']))
            for counts, picks in zip(period['counts'], period['picks']):
                self.assertEqual(len(counts), len(picks))
                self.assertTrue(all(count or pick is None for count, pick in zip(counts, picks)))

    def test_embedded_story_title_cannot_close_json_script(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            release, source = root / 'release', root / 'source'
            (release / 'stories').mkdir(parents=True)
            source.mkdir()
            (source / 'analytics.html').write_text('<html><head><link rel="stylesheet" href="__RELEASE__analytics.css"></head><body><script defer src="__RELEASE__analytics.js"></script></body></html>')
            (source / 'analytics.css').write_text('body {}')
            (source / 'analytics.js').write_text('window.ready = true;')
            title = '</script><script>alert(1)</script>'
            (release / 'stories' / '1.json').write_text(json.dumps([
                {'id': 42, 'time': 1704067200, 'title': title, 'score': 1}]))
            analytics.write(release, source, {'as_of': 1704067200, 'topics': [
                {'id': 1, 'name': 'A', 'slug': 'a'}]}, 'unused')
            html = (release / 'public' / 'analytics' / 'index.html').read_text()
            self.assertNotIn(title, html)
            self.assertIn('\\u003c/script>', html)
            self.assertEqual(html.count('</script>'), 2)
