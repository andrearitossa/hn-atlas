import math
import sqlite3
import unittest
from unittest.mock import patch

import attention
import catalog
import hn_sync

NOW = 1800000000
DAY = attention.DAY


def post(i, score=100, age=1, title=None, url=None):
    return dict(id=i, title=title or f'Independent subject number {i}',
                url=url or f'https://example.com/{i}', time=NOW-int(age*DAY), score=score, descendants=0)


class AttentionTests(unittest.TestCase):
    def test_three_day_decay_retains_older_major_story(self):
        # 3 days costs a factor of e, not exclusion; a high-impact older story wins.
        items = [post(1, score=101, age=0), post(2, score=301, age=3), post(3, score=1000, age=300)]
        self.assertEqual([p['id'] for p in attention.select(items, NOW)], [2, 1, 3])
        self.assertAlmostEqual(math.exp(-3 / attention.DECAY_DAYS), 1/math.e)

    def test_quiet_topics_keep_old_posts_and_exclude_future_low_score(self):
        items = [post(1, age=4000), post(2, age=5000), post(3, age=-1), post(4, score=4)]
        self.assertEqual([p['id'] for p in attention.select(items, NOW)], [1, 2])

    def test_duplicates_and_tracking_urls_do_not_take_two_slots(self):
        a = post(1, score=200, url='https://www.example.com/article/?utm_source=hn#intro')
        b = post(2, score=190, url='http://example.com/article')
        c = post(3, score=180, url='https://example.com/article?version=2')
        self.assertEqual([p['id'] for p in attention.select([a,b,c], NOW)], [1,3])

    def test_similar_titles_do_not_change_points_and_age_ranking(self):
        a = post(1, score=200, title='Acme releases new database storage engine')
        b = post(2, score=190, title='Acme releases database storage engine today')
        c = post(3, score=160, title='Postgres query planning explained')
        self.assertEqual([p['id'] for p in attention.select([a,b,c], NOW, 2)], [1,2])
        b['score'] = 500
        self.assertEqual([p['id'] for p in attention.select([a,b,c], NOW, 3)], [2,1,3])

    def test_same_domain_or_topic_word_does_not_change_ranking(self):
        items = [post(1, score=200, title='Rust compiler optimizes async state machines'),
                 post(2, score=190, title='Rust stabilizes never type'),
                 post(3, score=180, title='Python packaging changes')]
        self.assertEqual([p['id'] for p in attention.select(items, NOW)], [1,2,3])

    def test_different_products_with_boilerplate_headlines_remain_independent(self):
        items = [post(1, score=200, title='Claude Opus 5.5 Intelligence, Performance and Price Analysis (Max)'),
                 post(2, score=190, title='MiMo-v2.6-Pro: Intelligence, Performance and Price Analysis'),
                 post(3, score=180, title='Another independent announcement')]
        self.assertEqual([p['id'] for p in attention.select(items, NOW)], [1,2,3])

    def test_identical_titles_at_different_urls_keep_their_rank(self):
        title = 'Two parallel neural ectoderm progenitors contribute to the developing brain'
        items = [post(1, score=200, title=title), post(2, score=190, title=title),
                 post(3, score=180, title='Aging brains blend memories together instead of just forgetting them')]
        self.assertEqual([p['id'] for p in attention.select(items, NOW, 2)], [1,2])

    def test_ties_are_deterministic(self):
        items = [post(1),post(2)]
        self.assertEqual(attention.select(items,NOW), attention.select(items[::-1],NOW))


class FeaturedRefreshTests(unittest.TestCase):
    def test_old_picks_counts_deletions_and_replacements_refresh_together(self):
        c = sqlite3.connect(':memory:'); self.addCleanup(c.close)
        c.executescript(hn_sync.SCHEMA + 'CREATE TABLE story_topics(id INTEGER PRIMARY KEY,topic INTEGER);')
        for i in range(1,13):
            p = post(i, score=200-i, age=40, title=f'Article {i}')
            hn_sync.save_articles(c, [dict(p,type='story')])
            c.execute('INSERT INTO story_topics VALUES(?,1)',(i,))
        c.execute('UPDATE stories SET fetched_at=?',(NOW-100,))
        seen=[]
        def fetch(i):
            seen.append(i)
            if i==1: return dict(id=i, deleted=True)
            return dict(post(i,score=200-i,age=40,title=f'Article {i}'),type='story',descendants=99)
        class Pool:
            def map(self,fn,ids): return map(fn,ids)
        with patch.object(hn_sync,'fetch_item',side_effect=fetch), patch.object(hn_sync.time,'time',return_value=NOW):
            attention.refresh_featured(c,Pool(),NOW)
            attention.refresh_featured(c,Pool(),NOW)
        self.assertEqual(set(seen),set(range(1,12)))
        self.assertEqual(len(seen),11)  # no second fetch, including replacement
        picks=attention.trending(c,1,NOW)
        self.assertNotIn(1,[p['id'] for p in picks])
        self.assertTrue(all(p['descendants']==99 for p in picks))


if __name__ == '__main__': unittest.main()
