import asyncio
import unittest
from datetime import datetime, timezone

from database import connect
import server
import timeline
import test_browsing


class TimelineTests(unittest.TestCase):
    setUp = test_browsing.BrowsingTests.setUp

    def test_bounds_and_empty_period(self):
        result = server.topic_timeline(3, 30)
        self.assertEqual(result['id'], 3)
        self.assertEqual(sum(c['count'] for c in result['chapters']), 2)
        self.assertEqual(server.timeline_overview(90)['chapters'], server.topic_timeline(3, 90)['chapters'])
        with connect(self.path) as c:
            c.execute('UPDATE stories SET time=? WHERE id=3', (self.clock + 86400,))
            result = timeline.build(c, 3, 90, self.clock)
            self.assertEqual(sum(ch['count'] for ch in result['chapters']), 2)
            c.execute('DELETE FROM stories')
            self.assertEqual(timeline.build(c)['chapters'], [])

    def test_dedup_ranking_chronology_and_emerging_terms(self):
        self.clock = int(datetime(2026, 4, 30, tzinfo=timezone.utc).timestamp())
        with connect(self.path) as c:
            c.execute('DELETE FROM stories')
            c.execute('DELETE FROM story_topics')
            for ident, age, title, url, score in [
                (1, 60, 'Solar battery research', 'https://a.test/one', 200),
                (2, 59, 'Solar battery design', 'https://a.test/two', 100),
                (3, 20, 'Quantum chip research', 'https://b.test/three', 100),
                (4, 19, 'Quantum chip release', 'https://b.test/four', 200),
                (5, 18, 'Duplicate release', 'http://www.b.test/four#part', 300),
                (6, 17, 'Quantum chip production', 'https://b.test/six', 400),
            ]:
                c.execute('INSERT INTO stories(id,title,url,time,score,descendants,dead,deleted,fetched_at) VALUES(?,?,?,?,?,0,0,0,0)',
                          (ident,title,url,self.clock-age*86400,score))
                c.execute('INSERT INTO story_topics VALUES(?,3,.8)', (ident,))
            result = timeline.build(c, 3, 365, self.clock)
        chapters = result['chapters']
        self.assertEqual(chapters[0]['emerging'], [])
        self.assertIn('quantum', chapters[-1]['emerging'])
        self.assertEqual(chapters[-1]['headline'], 'Quantum chip production')
        self.assertEqual([p['id'] for p in chapters[-1]['posts']], [3,5,6])
        self.assertEqual(chapters[-1]['count'], 4)

    def test_history_spans_archive_and_balances_years(self):
        with connect(self.path) as c:
            c.execute('DELETE FROM stories')
            c.execute('DELETE FROM story_topics')
            for ident, year, score, url, dead in [
                (1,2015,30,'https://a.test/original',0),
                (2,2015,20,'https://a.test/second',0),
                (3,2025,900,'https://a.test/original#repost',0),
                (4,2025,800,'https://b.test/a',0),
                (5,2025,700,'https://b.test/b',0),
                (6,2025,600,'https://b.test/c',0),
                (7,2025,500,'https://b.test/d',0),
                (8,2025,9999,'https://b.test/dead',1),
                (9,2016,1,'https://b.test/quiet',0),
                (10,2030,9999,'https://b.test/future',0),
            ]:
                stamp = int(datetime(year,1,1,tzinfo=timezone.utc).timestamp())
                c.execute('INSERT INTO stories(id,title,url,time,score,descendants,dead,deleted,fetched_at) VALUES(?,?,?,?,?,0,?,0,0)',
                          (ident,f'Story {ident}',url,stamp,score,dead))
                c.execute('INSERT INTO story_topics VALUES(?,3,.8)', (ident,))
            end = int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp())
            result = timeline.history(c,3,end)
            self.assertEqual([ch['period'] for ch in result['chapters']], ['2015','2016','2025'])
            self.assertEqual([[p['id'] for p in ch['posts']] for ch in result['chapters']], [[1,2],[9],[4,5,6]])
            self.assertEqual(result['chapters'][-1]['count'],5)
            c.execute('UPDATE stories SET deleted=1')
            self.assertEqual(timeline.history(c,3,end)['chapters'], [])

    def test_zoom_levels_ranking_gaps_and_calendar_boundaries(self):
        with connect(self.path) as c:
            c.execute('DELETE FROM stories')
            c.execute('DELETE FROM story_topics')
            fixtures = [
                (1,'2024-12-31',100,'https://a.test/a',0),
                (2,'2025-01-01',90,'https://a.test/b',0),
                (3,'2025-01-02',80,'https://a.test/c',0),
                (4,'2025-01-03',70,'https://a.test/d',0),
                (5,'2025-01-04',60,'https://a.test/e',0),
                (6,'2025-01-05',50,'https://a.test/f',0),
                (7,'2025-01-06',40,'https://a.test/g',0),
                (8,'2025-01-02',85,'http://www.a.test/b#duplicate',0),
                (9,'2025-03-01',5,'https://a.test/quiet',0),
                (10,'2025-01-02',1000,'https://a.test/dead',1),
                (11,'2027-01-01',1000,'https://a.test/future',0),
            ]
            for ident,date,score,url,dead in fixtures:
                stamp=int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())
                c.execute('INSERT INTO stories(id,title,url,time,score,descendants,dead,deleted,fetched_at) VALUES(?,?,?,?,?,0,?,0,0)',
                          (ident,f'Story {ident}',url,stamp,score,dead))
                c.execute('INSERT INTO story_topics VALUES(?,3,.8)', (ident,))
            end=int(datetime(2025,3,2,tzinfo=timezone.utc).timestamp())
            result=timeline.zoom(c,3,end)
            years=result['levels']['year']
            self.assertEqual([[p['id'] for p in period['posts']] for period in years], [[1],[2,3,4,5,6]])
            self.assertEqual(sum(p['count'] for p in years),9)
            months=result['levels']['month']
            self.assertEqual([p['count'] for p in months],[1,7,0,1])
            self.assertEqual(months[2]['posts'],[])
            self.assertEqual([p['id'] for p in months[3]['posts']],[9])
            self.assertEqual([p['id'] for p in result['levels']['week'][-1]['posts']],[9])
            weeks=result['levels']['week']
            self.assertEqual(datetime.fromtimestamp(weeks[0]['start'],timezone.utc).strftime('%Y-%m-%d'),'2024-12-30')
            self.assertEqual([p['id'] for p in weeks[0]['posts']],[1,2,3])
            self.assertEqual(sum(p['count'] for p in weeks),9)
            for periods in result['levels'].values():
                self.assertLessEqual(periods[-1]['end'],end+1)
                for before,after in zip(periods,periods[1:]):
                    self.assertEqual(before['end'],after['start'])
            c.execute('UPDATE stories SET deleted=1')
            empty=timeline.zoom(c,3,end)
            self.assertIsNone(empty['start'])
            self.assertTrue(all(not periods for periods in empty['levels'].values()))

    def test_http_window_validation(self):
        async def request(query):
            messages = []
            async def receive():
                return {'type':'http.request','body':b'', 'more_body':False}
            async def send(message):
                messages.append(message)
            # Keep the selector awake when sandboxed thread notifications are delayed.
            async def heartbeat():
                while True:
                    await asyncio.sleep(0.01)
            wake = asyncio.create_task(heartbeat())
            self.addCleanup(wake.cancel)
            await server.app({'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
                              'method':'GET','scheme':'http','path':'/api/topics/3/timeline',
                              'query_string':query.encode(),'root_path':'','headers':[],
                              'server':('test',80),'client':('test',1234)}, receive, send)
            return messages[0]['status']
        for days in (0,30,90,365):
            self.assertEqual(asyncio.run(request(f'days={days}')), 200)
        self.assertEqual(asyncio.run(request('days=7')), 422)
        self.assertEqual(asyncio.run(request('view=zoom')), 200)
        self.assertEqual(asyncio.run(request('view=unknown')), 422)
