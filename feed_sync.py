"""Refresh the private D1 feed catalog from the daily ingestion snapshot."""
import argparse
import hashlib
import re
import sqlite3
import json
from pathlib import Path
import attention


def build(release, overview, edition, per_topic=20):
    release = Path(release)
    now = overview['as_of']
    by_id, memberships = {}, {}
    for topic in overview['topics']:
        posts = json.loads((release / 'recent' / f"{topic['id']}.json").read_text())
        live = [p for p in posts if now - 30 * 86400 <= p['time'] <= now
                and not p.get('dead') and not p.get('deleted')]
        for post in live:
            memberships.setdefault(post['id'], set()).add(topic['id'])
        for post in attention.select(live, now, limit=per_topic):
            by_id[post['id']] = post
    posts = []
    for post in sorted(by_id.values(), key=lambda p: p['id']):
        posts.append(dict(id=post['id'], title=post['title'], url=post.get('url'),
                          time=post['time'], hn_points=post.get('score') or 0,
                          topics=sorted(memberships[post['id']]),
                          article_key=hashlib.sha256(json.dumps(attention.article_key(post), separators=(',', ':')).encode()).hexdigest()))
    return dict(version=1, edition=edition, as_of=now,
                topics=[dict(id=t['id'], name=t['name'], slug=t['slug']) for t in overview['topics']],
                posts=posts)


SCHEMA = Path(__file__).parent / 'migrations/0007_feed_state.sql'
UPSERT = 'INSERT INTO feed_catalog(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload'
BACKFILL = """INSERT INTO feed_story_state(user_id,article_key,seen_at,opened_at)
 SELECT e.user_id,json_extract(j.value,'$.article_key'),max(e.created_at),
 max(CASE WHEN e.type='article_opened' THEN e.created_at END)
 FROM feed_events e JOIN json_each((SELECT payload FROM feed_catalog WHERE id=1),'$.posts') j
 ON e.story_id=json_extract(j.value,'$.id')
 WHERE e.type IN ('visible','article_opened')
 GROUP BY e.user_id,json_extract(j.value,'$.article_key')
 ON CONFLICT(user_id,article_key) DO UPDATE SET
 seen_at=max(seen_at,excluded.seen_at),opened_at=coalesce(opened_at,excluded.opened_at)"""


def payload(source):
    source = Path(source)
    match = re.search(r'name="hn-data" content="\./releases/([a-f0-9]{32})/"', (source / 'index.html').read_text())
    if not match:
        raise ValueError('Expected a published ingestion snapshot')
    release = source / 'releases' / match[1]
    overview = json.loads((release / 'topics.json').read_text())
    for count in (20, 15, 10, 5, 1):
        data = build(release, overview, match[1], count)
        value = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        # Leave headroom under D1's per-value limit; one atomic parameterized write.
        if len(value.encode()) <= 1500000:
            return value
    raise ValueError('Feed catalog exceeds the D1 size budget')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='dist')
    parser.add_argument('--local', help='Write a local SQLite database instead of remote D1')
    args = parser.parse_args()
    value = payload(args.source)  # Validate completely before touching the active catalog.
    if args.local:
        with sqlite3.connect(args.local) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript(SCHEMA.read_text())
            db.execute(UPSERT, (value,))
            db.execute(BACKFILL)
    else:
        from search_sync import Cloudflare
        config = json.loads((Path(__file__).parent / 'workers/newsletter-test/wrangler.jsonc').read_text())
        resource = config['d1_databases'][0]
        cloud = Cloudflare(dict(account_id=config['account_id'], database_id=resource['database_id']))
        cloud.sql(SCHEMA.read_text())
        result = cloud.api('POST', f"/d1/database/{resource['database_id']}/query", json={'sql':UPSERT, 'params':[value]})
        if not all(r.get('success') for r in result):
            raise RuntimeError('Feed catalog sync failed')
        cloud.sql(BACKFILL)
    print(f"Feed catalog ready: {len(json.loads(value)['posts'])} stories")


if __name__ == '__main__':
    main()
