"""Fetch, refresh, embed, classify, then maintain the permanent topic registry."""
from concurrent.futures import ThreadPoolExecutor

import production
import routing
import time
from embed import embed_pending
from hn_sync import catch_up, open_db, refresh_recent
from topics import iter_vectors


def classify_pending(conn, now=None):
    now = int(time.time()) if now is None else now
    production.setup(conn, now=now)
    version = production.input_version()
    columns = {r[1] for r in conn.execute('PRAGMA table_info(embeddings)')}
    version_filter = f' AND e.input_version={version}' if 'input_version' in columns else ''
    # Retire the old popularity-only review queue without paying for reviews.
    for ids, vectors in iter_vectors(conn,
            "e.id IN (SELECT id FROM classification_queue WHERE reason='high_impact')" + version_filter,size=1000):
        routing.store(conn,production.classify(conn,ids,vectors),now=now)
        conn.commit()
    where = ("s.dead=0 AND s.deleted=0 AND e.id NOT IN (SELECT id FROM story_topics) "
             "AND e.id NOT IN (SELECT id FROM classification_queue WHERE updated_at>=? OR attempts>=3 OR id IN (SELECT id FROM stories WHERE time<?))" + version_filter)
    for ids, vectors in iter_vectors(conn, where, (now-7*86400,now-28*86400), size=1000):
        routing.store(conn, production.classify(conn, ids, vectors), now=now)
        conn.commit()


def maintain(conn, now=None):
    """One weekly cadence for semantic review and structural changes."""
    now = int(time.time()) if now is None else now
    production.setup(conn, now=now)
    last = conn.execute("SELECT value FROM maintenance WHERE key='semantic_review'").fetchone()
    if not last or production.week_key(now)>production.week_key(last[0]):
        routing.review_pending(conn,now=now)
        conn.execute("INSERT OR REPLACE INTO maintenance VALUES ('semantic_review',?)",(now,))
        conn.commit()
    return production.weekly(conn,now=now)


def run(db='data/hackernews.db'):
    conn = open_db(db)
    try:
        now = int(time.time())
        production.setup(conn,now=now)
        last = conn.execute("SELECT value FROM maintenance WHERE key='pipeline'").fetchone()
        if last and production.week_key(now)<=production.week_key(last[0]):
            print('Weekly update already completed; nothing due.')
            return False
        with ThreadPoolExecutor(32) as pool:
            catch_up(conn, pool)
            refresh_recent(conn, pool, days=14, now=now, fetched_before=now)
        embed_pending(conn, version=production.input_version())
        classify_pending(conn,now=now)
        events = maintain(conn,now=now)
        conn.execute("INSERT OR REPLACE INTO maintenance VALUES ('pipeline',?)",(now,))
        conn.commit()
        print('Topic changes:', events)
        return True
    finally:
        conn.close()


if __name__ == '__main__':
    run()
