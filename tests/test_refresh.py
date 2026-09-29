import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import hn_sync
import topics
import refresh
import routing

DAY = refresh.DAY
NOW = 1_780_000_000 + 15 * DAY   # mid-month
A, B = 1000, 1001                # "Databases" and "Space"


def direction(i, noise=0.0, seed=0):
    v = np.zeros(8, 'f4'); v[i] = 1
    return v + np.random.default_rng(seed).normal(0, noise, 8).astype('f4')


class World:
    """A two-topic map in a temporary database; the test plays the role of HN."""

    def __init__(self, test):
        legacy = patch.object(refresh, 'classify', refresh.classify_reviewed)
        legacy.start(); test.addCleanup(legacy.stop)
        audit = patch.object(refresh.map_health, 'audit', return_value={'topics':2,'corrected':0})
        audit.start(); test.addCleanup(audit.stop)
        reviewer = patch.object(routing, 'semantic_decisions',
            side_effect=lambda topics, stories: [dict(id=s['id'], topic=s['current_topic']) for s in stories])
        reviewer.start(); test.addCleanup(reviewer.stop)
        folder = Path(tempfile.mkdtemp()); test.addCleanup(lambda: [p.unlink() for p in folder.iterdir()])
        centers = np.stack([direction(0), direction(1)])
        np.savez(folder / 'model.npz', mean=np.zeros(8, 'f4'), topic_ids=np.array([A, B]), centroids=centers,
                 prototype_centroids=centers, prototype_topic_ids=np.array([A, B]))
        patcher = patch.object(refresh, 'MODEL_PATH', str(folder / 'model.npz')); patcher.start(); test.addCleanup(patcher.stop)
        self.conn = sqlite3.connect(folder / 'map.db'); test.addCleanup(self.conn.close)
        self.conn.executescript(hn_sync.SCHEMA + topics.TOPIC_SCHEMA + topics.SCHEMA + routing.SCHEMA +
            'CREATE TABLE embeddings(id INTEGER PRIMARY KEY, model TEXT, vec BLOB, input_version INTEGER, input_hash TEXT);')
        for ident, name, x, axis in [(A, 'Databases', .2, 0), (B, 'Space', .8, 1)]:
            self.conn.execute('INSERT INTO topics VALUES (?,?,?,0,0,?,.5)', (ident, name, name, x))
            self.conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,0)', (ident, direction(axis).tobytes()))
        self.conn.commit()
        self.incoming, self.vectors, self.updates, self.next_id = [], {}, {}, 1

    def post(self, vector, when, site='example.com', score=10, title='A story'):
        ident = self.next_id; self.next_id += 1
        self.incoming.append((ident, 'story', 'pg', when, title, f'https://{site}/{ident}', None, score, 0, 0, 0, when))
        self.vectors[ident] = vector
        return ident

    def fetch(self, conn, now):
        conn.executemany('INSERT INTO stories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [p for p in self.incoming if p[3] <= now])
        self.incoming = [p for p in self.incoming if p[3] > now]
        conn.executemany('UPDATE stories SET score=?, descendants=? WHERE id=?',
                         [(s, c, i) for i, (s, c) in self.updates.items()])

    def embed(self, conn, now):
        conn.executemany('INSERT OR IGNORE INTO embeddings VALUES (?,?,?,2,NULL)',
                         [(i, 'test', v.astype('<f4').tobytes()) for i, v in self.vectors.items()
                          if conn.execute('SELECT 1 FROM stories WHERE id=?', (i,)).fetchone()])

    def run(self, now):
        return refresh.refresh(self.conn, now, fetch=self.fetch, embed=self.embed)

    def topic_of(self, ident):
        row = self.conn.execute('SELECT topic FROM story_topics WHERE id=?', (ident,)).fetchone()
        return row and row[0]

class ReviewedRoutingTests(unittest.TestCase):
    def test_subject_review_corrects_confident_vectors_and_survives_refiling(self):
        world = World(self)
        story = world.post(direction(0), NOW, title='A space mission')
        with patch.object(routing, 'semantic_decisions', return_value=[dict(id=story, topic=B)]) as review:
            world.run(NOW)
            self.assertEqual(world.topic_of(story), B)
            self.assertEqual(review.call_args.args[1][0]['current_topic'], A)
            world.conn.execute('DELETE FROM story_topics WHERE id=?', (story,))
            refresh.classify(world.conn, NOW, review=False)
            self.assertEqual(world.topic_of(story), B)
            self.assertEqual(review.call_count, 1)
            # The stored diagnostic is the actual fit to Space, not Databases' score.
            self.assertEqual(world.conn.execute('SELECT sim FROM story_topics WHERE id=?', (story,)).fetchone()[0], 0)

    def test_review_can_abstain_and_rechecks_changed_content(self):
        world = World(self)
        story = world.post(direction(0), NOW, title='A misleading headline')
        with patch.object(routing, 'semantic_decisions', return_value=[dict(id=story, topic=None)]) as review:
            world.run(NOW)
            self.assertIsNone(world.topic_of(story))
            world.conn.execute('DELETE FROM classification_queue WHERE id=?', (story,))
            refresh.classify(world.conn, NOW)
            self.assertEqual(review.call_count, 1)
            world.conn.execute('UPDATE stories SET title=? WHERE id=?', ('Changed subject', story))
            world.conn.execute('DELETE FROM classification_queue WHERE id=?', (story,))
            refresh.classify(world.conn, NOW)
            self.assertEqual(review.call_count, 2)

    def test_failed_subject_review_does_not_publish_vector_guess(self):
        world = World(self)
        story = world.post(direction(0), NOW)
        world.fetch(world.conn, NOW)
        world.embed(world.conn, NOW)
        world.conn.commit()
        with patch.object(routing, 'semantic_decisions', side_effect=ValueError('Incomplete review')):
            with self.assertRaisesRegex(ValueError, 'Incomplete review'):
                world.run(NOW)
        self.assertIsNone(world.topic_of(story))
        self.assertEqual(world.conn.execute('SELECT count(*) FROM routing_reviews').fetchone()[0], 0)
        self.assertIsNone(world.conn.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone())
        world.run(NOW)
        self.assertEqual(world.topic_of(story), A)

    def test_multiple_review_batches_file_every_story_once(self):
        world = World(self)
        stories = [world.post(direction(0), NOW, title=f'Story {i}') for i in range(205)]
        with patch.object(routing, 'semantic_decisions', side_effect=lambda topics, batch:
                          [dict(id=s['id'], topic=B) for s in batch]) as review:
            world.run(NOW)
        self.assertEqual(sorted(len(call.args[1]) for call in review.call_args_list), [5, 100, 100])
        self.assertTrue(all(world.topic_of(story) == B for story in stories))
        self.assertEqual(world.conn.execute('SELECT count(*) FROM routing_reviews').fetchone()[0], 205)

    def test_new_posts_are_filed_and_points_and_comments_follow_hn(self):
        world = World(self)
        world.run(NOW)
        story = world.post(direction(0, .1), NOW + 3600, score=1)
        offtopic = world.post(direction(5), NOW + 3600)
        self.assertEqual(world.run(NOW + DAY)['filed'], 1)
        self.assertEqual(world.topic_of(story), A)
        self.assertIsNone(world.topic_of(offtopic))           # waits instead of being misfiled
        world.updates[story] = (250, 91)
        world.run(NOW + 2 * DAY)
        self.assertEqual(world.conn.execute('SELECT score, descendants FROM stories WHERE id=?', (story,)).fetchone(), (250, 91))

    def test_counts_catch_up_after_missed_days_across_sixty_day_window(self):
        world = World(self)
        story = world.post(direction(0), NOW - 55 * DAY, score=1)
        stale = world.post(direction(0), NOW - 61 * DAY, score=1)
        world.run(NOW)
        def hn(ident):
            return dict(id=ident, type='story', time=NOW - 55 * DAY,
                        title='A story', url=f'https://example.com/{ident}', score=250, descendants=91)
        with patch.object(hn_sync, 'catch_up'), patch.object(hn_sync, 'fetch_item', side_effect=hn) as fetch:
            refresh.fetch_from_hn(world.conn, NOW)      # scores can mature long after publication
        fetch.assert_called_once_with(story)
        self.assertEqual(world.conn.execute('SELECT score, descendants FROM stories WHERE id=?', (story,)).fetchone(), (250, 91))
        self.assertEqual(world.conn.execute('SELECT score FROM stories WHERE id=?', (stale,)).fetchone()[0], 1)

    def test_newly_filed_old_featured_story_is_refetched_before_run_completes(self):
        world = World(self)
        story = world.post(direction(0), NOW - 40 * DAY, score=50)
        world.fetch(world.conn, NOW)
        world.embed(world.conn, NOW)
        world.conn.execute('UPDATE stories SET fetched_at=?', (NOW - DAY,))
        world.conn.commit()
        hn = dict(id=story, type='story', time=NOW - 40 * DAY, title='A story',
                  url=f'https://example.com/{story}', score=250, descendants=91)
        with patch.object(hn_sync, 'catch_up'), patch.object(hn_sync, 'fetch_item', return_value=hn) as fetch:
            refresh.refresh(world.conn, NOW, embed=world.embed)
        fetch.assert_called_once_with(story)
        self.assertEqual(world.topic_of(story), A)
        self.assertEqual(world.conn.execute('SELECT score, descendants FROM stories WHERE id=?', (story,)).fetchone(), (250, 91))

    def test_insufficient_monthly_evidence_keeps_unmatched_posts_unfiled(self):
        world = World(self)
        unmatched = world.post(direction(5), NOW)
        report = world.run(NOW)
        self.assertEqual(report['filed'], 0)
        self.assertIsNone(world.topic_of(unmatched))
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topics').fetchone()[0], 2)
        self.assertEqual(report['topics'], [])

class MonthlyDiscoveryTests(unittest.TestCase):
    def test_scheduled_daily_refresh_can_leave_discovery_aside(self):
        world = World(self)
        with patch.object(refresh, 'discover') as discover:
            report = refresh.refresh(world.conn, NOW, fetch=world.fetch, embed=world.embed,
                                     discover_topics=False)
        discover.assert_not_called()
        self.assertNotIn('topics', report)
        self.assertIsNone(world.conn.execute("SELECT value FROM maintenance WHERE key='discover'").fetchone())
        self.assertEqual(world.conn.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone()[0], NOW)

    def test_first_run_monthly_once_and_missed_month_catchup(self):
        world = World(self)
        with patch.object(refresh, 'discover', return_value=[]) as discover:
            world.run(NOW)
            world.run(NOW + DAY)
            self.assertEqual(discover.call_count, 1)
            world.run(NOW + 40 * DAY)
            self.assertEqual(discover.call_count, 2)
            world.run(NOW + 140 * DAY)
            self.assertEqual(discover.call_count, 3)  # One current check, not historical replays.
            self.assertEqual(world.conn.execute("SELECT value FROM maintenance WHERE key='discover'").fetchone()[0], NOW + 140 * DAY)

    def test_health_report_and_checkpoint_roll_back_if_discovery_fails(self):
        world = World(self)
        def audit(conn, now):
            conn.execute("INSERT INTO topic_health VALUES (?, ?, 1, 1, 1, 0, 0, '{}')", (A, now))
            return {'topics': 2}
        with patch.object(refresh.map_health, 'audit', side_effect=audit), \
             patch.object(refresh, 'discover', side_effect=RuntimeError('discovery failed')):
            with self.assertRaises(RuntimeError): world.run(NOW)
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topic_health').fetchone()[0], 0)
        self.assertIsNone(world.conn.execute("SELECT value FROM maintenance WHERE key='discover'").fetchone())

    def test_failed_monthly_check_rolls_back_and_retries(self):
        world = World(self)
        def fail(conn, now, decide):
            conn.execute("INSERT INTO topic_changes VALUES (?,'skip',NULL,'Failed check',0,'test','[]')", (now,))
            raise RuntimeError('review failed')
        with patch.object(refresh, 'discover', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'review failed'):
                world.run(NOW)
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topic_changes').fetchone()[0], 0)
        self.assertEqual(world.conn.execute('SELECT count(*) FROM maintenance').fetchone()[0], 0)
        with patch.object(refresh, 'discover', return_value=[]) as discover:
            world.run(NOW)
            discover.assert_called_once()

    def test_supported_new_topic_persists_and_files_future_posts(self):
        world = World(self)
        older, recent = [], []
        for i in range(30):
            world.post(direction(0), NOW - 45 * DAY, site=f'database{i}.com', score=100)
            older.append(world.post(direction(5), NOW - 45 * DAY, site=f'proof{i}.com', score=100))
            recent.append(world.post(direction(5), NOW - 10 * DAY, site=f'recent{i}.com', score=100))
        def decide(groups, topics):
            return [dict(group=i, action='new', name='Robotics', description='Robotics research and engineering',
                         reason='A durable independent field',
                         recent_matches=list(range(len(g['recent_sample']))),
                         older_matches=list(range(len(g['proof']['sample'])))) for i,g in enumerate(groups)]
        report = refresh.refresh(world.conn, NOW, fetch=world.fetch, embed=world.embed, decide=decide)
        self.assertEqual([c['action'] for c in report['topics']], ['new'])
        new = report['topics'][0]['topic']
        self.assertTrue(all(world.topic_of(i) == new for i in older + recent))
        self.assertIn(new, refresh.load_model(world.conn)['prototype_topic_ids'])
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topic_centers').fetchone()[0], 1)
        future = world.post(direction(5), NOW + DAY)
        world.run(NOW + DAY)
        self.assertEqual(world.topic_of(future), new)
        self.assertEqual(world.conn.execute('SELECT count(*) FROM topics').fetchone()[0], 3)

    def test_new_topics_need_independent_quality_and_valid_sample_evidence(self):
        proposal = dict(action='new',name='Robotics',description='A durable interest')
        level = dict(posts=30,cohesion=.8)
        proof = dict(gathered=35,taken=0,cohesion=.9)
        self.assertEqual(refresh.verdict(proposal,proof,level,{A:'Databases'},0)[0], 'new')
        for weak in [dict(proof,gathered=24), dict(proof,taken=40), dict(proof,cohesion=.7)]:
            self.assertEqual(refresh.verdict(proposal,weak,level,{A:'Databases'},0)[0], 'skip')
        self.assertEqual(refresh.verdict(proposal,proof,level,{A:'Databases'},refresh.MAX_NEW)[0], 'skip')
        group = dict(recent_sample=[{}]*10,proof={'sample':[{}]*10})
        self.assertTrue(refresh.relevant(dict(recent_matches=list(range(8)),older_matches=list(range(8))),group))
        self.assertFalse(refresh.relevant(dict(recent_matches=list(range(8)),older_matches=list(range(7))),group))
        self.assertFalse(refresh.relevant(dict(recent_matches=[0]*8,older_matches=list(range(8))),group))

class DiscoveryRelevanceTests(unittest.TestCase):
    def test_repeated_low_vote_subject_can_reach_editor(self):
        world=World(self)
        for i in range(30):
            world.post(direction(5),NOW-10*DAY,site=f'source{i}.com',score=1,title=f'Robotics research {i}')
        world.run(NOW)
        groups=refresh.candidates(world.conn,NOW,refresh.load_model(world.conn))
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['posts'],30)

    def test_incomplete_editor_answer_fails_instead_of_marking_month_done(self):
        group=dict(posts=30,titles=[],nearest=[],recent_sample=[],proof=dict(gathered=30,taken=0,sample=[]))
        with patch('llm.ask_json',return_value={'decisions':[]}):
            with self.assertRaisesRegex(ValueError,'Incomplete monthly'):
                refresh.judge([group],[])
