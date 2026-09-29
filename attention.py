"""Recent attention: HN points with a three-day exponential decay and URL deduplication.

No age cutoff. Work in log space so very quiet topics retain a stable ordering
without floating-point underflow. Counts are HN points, not estimated vote velocity.
"""
import math
import topics as topic_registry
from urllib.parse import parse_qsl, urlencode, urlsplit

DAY = 86400
DECAY_DAYS = 3
POST = 's.id,s.title,s.url,s.time,s.score,s.descendants'


def article_key(post):
    try:
        url = urlsplit(post.get('url') or '')
        if url.scheme in ('http', 'https') and url.netloc:
            query = urlencode(sorted((k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                                     if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')))
            return (url.netloc.lower().removeprefix('www.'), url.path.rstrip('/'), query)
    except ValueError:
        pass
    return post['id']


def select(posts, now, limit=10, decay_days=DECAY_DAYS):
    if limit <= 0:
        return []
    candidates = []
    for p in posts:
        if p['time'] > now or (p['score'] or 0) < 5:
            continue
        score = math.log(p['score'] - 1) - (now - p['time']) / (DAY * decay_days)
        candidates.append((p, score, article_key(p)))
    candidates.sort(key=lambda item: (item[1], item[0]['id']), reverse=True)
    chosen, seen = [], set()
    for p, _, key in candidates:
        if key in seen:
            continue
        chosen.append(p)
        seen.add(key)
        if len(chosen) == limit:
            break
    return chosen


def posts(conn, topic, now, *, since=0):
    cursor = conn.execute(f'SELECT {POST} FROM stories s JOIN {topic_registry.memberships(conn)} st USING(id) '
                          'WHERE st.topic=? AND s.dead=0 AND s.deleted=0 AND s.time<=? AND s.time>? AND s.score>=5',
                          (topic, now, since))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def trending(conn, topic, now, limit=10):
    return select(posts(conn, topic, now), now, limit)


def refresh_featured(conn, pool, now, *, commit=True):
    """Refresh older featured posts too, until every resulting pick was checked.

    Re-selection matters: deletions or reduced scores can reveal another older
    story. A null HN response is attempted only once per run. Recent posts have
    already been refreshed by the caller; skip those and catch-up fetches.
    """
    import hn_sync
    topics = [r[0] for r in conn.execute(f'SELECT DISTINCT topic FROM {topic_registry.memberships(conn)}')]
    checked = {r[0] for r in conn.execute('SELECT id FROM stories WHERE fetched_at>=?', (now,))}
    total = 0
    while True:
        ids = {p['id'] for topic in topics for p in trending(conn, topic, now)} - checked
        if not ids:
            return total
        total += hn_sync.save_articles(conn, pool.map(hn_sync.fetch_item, sorted(ids)))
        checked.update(ids)
        if commit:
            conn.commit()
