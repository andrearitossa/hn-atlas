"""Evidence-based timeline chapters, shared by live and static browsing."""
from collections import Counter
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit, urlunsplit

import catalog

STOP = set('a an the and or but of to in on for from with by at as is are was were be been it its this that these those how why what when who new using use can now has have had not into your you we our about more than will show ask hn'.split())


def terms(title):
    return {w for w in re.findall(r'[a-z][a-z0-9+#-]*', (title or '').lower())
            if len(w) > 2 and w not in STOP}


def article_key(post):
    try:
        url = urlsplit(post['url'] or '')
        if url.scheme in ('http', 'https') and url.netloc:
            return urlunsplit(('', url.netloc.lower().removeprefix('www.'), url.path.rstrip('/'), url.query, ''))
    except ValueError:
        pass
    return str(post['id'])


def build(c, topic_id=None, days=90, as_of=None):
    if days not in (30, 90, 365):
        raise ValueError('Timeline days must be 30, 90, or 365')
    end = catalog.now(c) if as_of is None else as_of
    start = end - days * catalog.DAY
    params = [start, end]
    where = 's.dead=0 AND s.deleted=0 AND s.time>=? AND s.time<=?'
    if topic_id is not None:
        where += ' AND st.topic=?'
        params.append(topic_id)
    rows = c.execute(f'SELECT {catalog.POST}, st.topic FROM stories s '
                     f"JOIN {catalog.topics.memberships(c) if topic_id is not None else 'story_topics'} st USING(id) WHERE " + where +
                     ' ORDER BY coalesce(s.score,0) DESC,coalesce(s.descendants,0) DESC,s.id DESC', params)
    buckets = {}
    for row in rows:
        post = dict(row)
        date = datetime.fromtimestamp(post['time'], timezone.utc)
        if days == 30:
            stamp = date.strftime('%Y-%m-%d')
        elif days == 90:
            stamp = datetime.fromtimestamp(post['time'] - date.weekday() * catalog.DAY, timezone.utc).strftime('%Y-%m-%d')
        else:
            stamp = date.strftime('%Y-%m')
        buckets.setdefault(stamp, []).append(post)
    chapters, previous = [], set()
    for stamp, posts in sorted(buckets.items()):
        unique, seen = [], set()
        for post in posts:
            key = article_key(post)
            if key not in seen:
                unique.append(post)
                seen.add(key)
        # Terms describe the leading coverage; they are not generated claims about events.
        counts = Counter(word for post in unique[:20] for word in terms(post['title']))
        prominent = [word for word, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if count >= 2]
        emerging = [word for word in prominent if word not in previous][:4] if chapters else []
        chapters.append(dict(period=stamp, count=len(posts), posts=sorted(unique[:3], key=lambda p: (p['time'], p['id'])),
                             headline=unique[0]['title'], emerging=emerging))
        previous = set(prominent)
    return dict(id=topic_id, as_of=end, days=days, start=start,
                interval={30: 'day', 90: 'week', 365: 'month'}[days], chapters=chapters)


def history(c, topic_id, as_of=None):
    """A reading path across the entire topic archive, balanced across years."""
    end = catalog.now(c) if as_of is None else as_of
    rows = [dict(row) for row in c.execute(
        f'SELECT {catalog.POST} FROM stories s JOIN {catalog.topics.memberships(c)} st USING(id) '
        'WHERE st.topic=? AND s.dead=0 AND s.deleted=0 AND s.time<=? '
        'ORDER BY s.time,s.id', (topic_id, end))]
    return history_posts(rows, topic_id, end)


def history_posts(rows, topic_id, end):
    rows = sorted((p for p in rows if p['time'] <= end), key=lambda p: (p['time'], p['id']))
    buckets = {}
    for post in rows:
        year = datetime.fromtimestamp(post['time'], timezone.utc).strftime('%Y')
        buckets.setdefault(year, []).append(post)
    chapters, featured = [], set()
    for year, posts in buckets.items():
        ranked = sorted(posts, key=lambda p: (p['score'] or 0, p['descendants'] or 0, p['id']), reverse=True)
        picks = []
        for post in ranked:
            key = article_key(post)
            if key in featured:
                continue
            featured.add(key)
            picks.append(dict(post, rank=len(picks) + 1))
            if len(picks) == 3:
                break
        chapters.append(dict(period=year, count=len(posts),
                             posts=sorted(picks, key=lambda p: (p['time'], p['id']))))
    return dict(id=topic_id, as_of=end, start=rows[0]['time'] if rows else None,
                interval='year', chapters=chapters)


def zoom(c, topic_id, as_of=None):
    """Nested calendar resolutions for a continuous, zoomable topic history."""
    end = catalog.now(c) if as_of is None else as_of
    rows = [dict(row) for row in c.execute(
        f'SELECT {catalog.POST} FROM stories s JOIN {catalog.topics.memberships(c)} st USING(id) '
        'WHERE st.topic=? AND s.dead=0 AND s.deleted=0 AND s.time<=? '
        'ORDER BY coalesce(s.score,0) DESC,coalesce(s.descendants,0) DESC,s.id DESC',
        (topic_id, end))]
    return zoom_posts(rows, topic_id, end)


def zoom_posts(rows, topic_id, end):
    from datetime import timedelta

    rows = sorted((p for p in rows if p['time'] <= end),
                  key=lambda p: (p['score'] or 0, p['descendants'] or 0, p['id']), reverse=True)
    start = min((p['time'] for p in rows), default=None)
    levels = {}
    for interval in ('year', 'month', 'week'):
        buckets = {}
        for post in rows:
            date = datetime.fromtimestamp(post['time'], timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            if interval == 'year':
                date = date.replace(month=1, day=1)
            elif interval == 'month':
                date = date.replace(day=1)
            else:
                date -= timedelta(days=date.weekday())
            stamp = int(date.timestamp())
            bucket = buckets.setdefault(stamp, {'count': 0, 'posts': [], 'seen': set()})
            bucket['count'] += 1
            key = article_key(post)
            if key not in bucket['seen']:
                bucket['seen'].add(key)
                if len(bucket['posts']) < (5 if interval == 'year' else 3):
                    bucket['posts'].append(post)
        periods = []
        if buckets:
            date = datetime.fromtimestamp(min(buckets), timezone.utc)
            last = max(buckets)
            while int(date.timestamp()) <= last:
                stamp = int(date.timestamp())
                bucket = buckets.get(stamp, {'count': 0, 'posts': []})
                if interval == 'year':
                    following = date.replace(year=date.year + 1)
                elif interval == 'month':
                    following = date.replace(year=date.year + (date.month == 12), month=date.month % 12 + 1)
                else:
                    following = date + timedelta(days=7)
                periods.append(dict(start=stamp, end=min(int(following.timestamp()), end + 1),
                                    count=bucket['count'], posts=bucket['posts']))
                date = following
        levels[interval] = periods
    return dict(id=topic_id, as_of=end, start=start, levels=levels)
