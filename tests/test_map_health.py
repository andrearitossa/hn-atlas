import unittest
from unittest.mock import Mock

import map_health
import refresh
from test_refresh import World, NOW, DAY, A, B, direction

AUDIT = map_health.audit


class TopicHealthTests(unittest.TestCase):
    def world(self):
        world=World(self)
        world.conn.executescript(refresh.SCHEMA+map_health.SCHEMA)
        return world

    def misfile(self, world):
        story=world.post(direction(0),NOW,title='A new database engine')
        world.fetch(world.conn,NOW);world.embed(world.conn,NOW)
        world.conn.execute('INSERT INTO story_topics VALUES (?,?,.2,0)',(story,B))
        return story

    def reviewer(self, ident):
        return lambda batch:[dict(id=p['id'],off_topic=[ident] if any(s['id']==ident for s in p['stories']) else [],reason='Wrong subject') for p in batch]

    def test_confirmed_misfile_moves_and_refreshes_cache_and_metrics(self):
        world=self.world();story=self.misfile(world)
        classifier=Mock(return_value=[dict(id=story,topic=A)])
        result=AUDIT(world.conn,NOW,self.reviewer(story),classifier)
        self.assertEqual(world.topic_of(story),A)
        self.assertEqual((result['flagged'],result['corrected']),(1,1))
        self.assertNotIn('current_topic',classifier.call_args.args[1][0])
        self.assertEqual(world.conn.execute('SELECT topic FROM routing_reviews WHERE id=?',(story,)).fetchone()[0],A)
        self.assertEqual(world.conn.execute('SELECT corrected FROM topic_health WHERE topic=?',(B,)).fetchone()[0],1)

    def test_disagreement_and_editorial_override_do_not_move_story(self):
        world=self.world();story=self.misfile(world)
        result=AUDIT(world.conn,NOW,self.reviewer(story),lambda *_:[dict(id=story,topic=B)])
        self.assertEqual(result['corrected'],0)
        world.conn.execute('CREATE TABLE editorial_decisions(id INTEGER PRIMARY KEY,topic INTEGER)')
        world.conn.execute('INSERT INTO editorial_decisions VALUES (?,?)',(story,B))
        result=AUDIT(world.conn,NOW,self.reviewer(story),lambda *_:[dict(id=story,topic=A)])
        self.assertEqual(result['corrected'],0);self.assertEqual(world.topic_of(story),B)

    def test_uncovered_subject_returns_to_discovery_queue(self):
        world=self.world();story=self.misfile(world)
        AUDIT(world.conn,NOW,self.reviewer(story),lambda *_:[dict(id=story,topic=None)])
        self.assertIsNone(world.topic_of(story))
        self.assertEqual(world.conn.execute('SELECT reason FROM classification_queue WHERE id=?',(story,)).fetchone()[0],'monthly_mismatch')

    def test_invalid_or_partial_reviews_fail_without_health_marker(self):
        world=self.world();story=self.misfile(world)
        for bad in [[],[dict(id=B,off_topic=[999],reason='bad')]]:
            with self.assertRaises(ValueError):AUDIT(world.conn,NOW,lambda _:bad,Mock())
        with self.assertRaises(ValueError):AUDIT(world.conn,NOW,self.reviewer(story),lambda *_:[])
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topic_health').fetchone()[0],0)
        self.assertEqual(world.topic_of(story),B)

    def test_quiet_topics_are_reported_and_retained_without_api(self):
        world=self.world();review=Mock();classify=Mock()
        result=AUDIT(world.conn,NOW,review,classify)
        self.assertEqual(result['quiet'],2)
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topics').fetchone()[0],2)
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topic_health').fetchone()[0],2)
        review.assert_not_called();classify.assert_not_called()

    def test_sample_contains_popular_weak_and_time_spread_posts(self):
        world=self.world()
        ids=[world.post(direction(0),NOW-(i+1)*DAY,score=1000 if i==15 else 10) for i in range(40)]
        world.fetch(world.conn,NOW);world.embed(world.conn,NOW)
        world.conn.executemany('INSERT INTO story_topics VALUES (?,?,?,0)',[(ident,A,.01 if i==20 else .9) for i,ident in enumerate(ids)])
        packet=next(p for p in map_health.samples(world.conn,NOW) if p['id']==A)
        chosen={s['id'] for s in packet['stories']}
        self.assertEqual(len(chosen),12)
        self.assertTrue({ids[0],ids[-1],ids[15],ids[20]}<=chosen)
        self.assertEqual((packet['recent_posts'],packet['previous_posts']),(29,11))
