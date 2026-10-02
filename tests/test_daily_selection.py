import sqlite3
import unittest
from unittest.mock import patch

import daily_selection as daily


class DailySelectionTests(unittest.TestCase):
    def fixture(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE stories(id INTEGER,title TEXT,url TEXT,text TEXT,time INTEGER,score INTEGER,descendants INTEGER,dead INTEGER,deleted INTEGER)')
        return conn

    def test_age_adjustment(self):
        now = 200000
        young = dict(time=now-3600, score=101)
        old = dict(time=now-24*3600, score=501)
        self.assertGreater(daily.relevance(young, now), daily.relevance(old, now))
        self.assertGreater(daily.relevance(dict(time=now-3600, score=201), now), daily.relevance(young, now))

    def test_hotness_half_life_and_vote_tradeoff(self):
        now = 200000
        for age in (0, 6, 12, 18):
            fresh = daily.relevance(dict(time=now-age*3600, score=101), now)
            older = daily.relevance(dict(time=now-(age+6)*3600, score=101), now)
            doubled = daily.relevance(dict(time=now-(age+6)*3600, score=201), now)
            self.assertAlmostEqual(fresh, 2*older)
            self.assertAlmostEqual(fresh, doubled)

    def test_hard_window_future_deletions_and_duplicates(self):
        conn = self.fixture()
        now = 200000
        for id, age, dead, deleted, url in [(1,24*3600,0,0,'https://a.com'), (2,24*3600+1,0,0,'https://b.com'),
                (3,-1,0,0,'https://c.com'),(4,60,1,0,'https://d.com'),(5,60,0,1,'https://e.com'),
                (6,3600,0,0,'https://a.com?utm_source=hn')]:
            conn.execute('INSERT INTO stories VALUES(?,?,?,?,?,?,?,?,?)', (id,str(id),url,'',now-age,100,0,dead,deleted))
        self.assertEqual([p['id'] for p in daily.ranked(conn, now)], [6])
        conn.execute('DELETE FROM stories WHERE id=6')
        self.assertEqual([p['id'] for p in daily.ranked(conn, now)], [1])

    def test_full_source_reaches_model(self):
        source = 'Complete article. '*2000 + 'FINAL IMPORTANT DETAIL'
        post = dict(id=1,title='A story',url='https://example.com',text='')
        with patch('newsletter.page_text', return_value=source) as fetch, patch('llm.ask_json', return_value={'overview':'A clear overview.'}) as ask:
            result = daily.overview(post)
        fetch.assert_called_once_with(post['url'], full=True)
        self.assertIn('FINAL IMPORTANT DETAIL', ask.call_args.args[0])
        self.assertEqual(ask.call_args.kwargs['model'], 'gpt-6-luna')
        self.assertEqual(result['summary'], 'A clear overview.')

    def test_failed_overview_keeps_exact_top_ten(self):
        posts = [dict(id=i) for i in range(12)]
        def describe(post):
            if post['id']==2:
                raise ValueError('Unreadable')
            return dict(post,summary='Overview')
        with patch.object(daily, 'ranked', return_value=iter(posts)), patch.object(daily, 'overview', side_effect=describe), self.assertLogs(daily.LOG, level='ERROR'):
            picks = daily.prepare(None, 0)
        self.assertEqual([p['id'] for p in picks], list(range(10)))
        self.assertEqual(picks[2]['summary'], '')

    def test_all_overviews_can_fail_without_losing_stories(self):
        posts = [dict(id=i,title=f'Story {i}',url=f'https://example.com/{i}') for i in range(10)]
        with patch.object(daily, 'ranked', return_value=iter(posts)), patch.object(daily, 'overview', side_effect=RuntimeError('API unavailable')), self.assertLogs(daily.LOG, level='ERROR'):
            picks = daily.prepare(None, 0)
        html = daily.render(picks, 200000)
        self.assertEqual(len(picks), 10)
        self.assertEqual(html.count('<section '), 10)
        self.assertNotIn('<p style="line-height:1.7">', html)
        self.assertIn('last 24 hours', html)
        for post in posts:
            self.assertIn(post['url'], html)

    def test_short_pool_preserves_cutoff_and_sends_available_stories(self):
        posts = [dict(id=1)]
        with patch.object(daily, 'ranked', return_value=iter(posts)), patch.object(daily, 'overview', return_value=dict(id=1,summary='Overview')):
            self.assertEqual(len(daily.prepare(None, 0)), 1)
