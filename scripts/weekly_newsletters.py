"""Prepare one HTML newsletter per topic in D1, then ask the Worker to send it."""
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import fcntl
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DB, load_jsonc
import newsletter
import topics as topic_registry
from database import connect

from cloudflare import d1_query as query


def edition_at(now):
    """Most recent Sunday 18:00 Stockholm; Monday catch-up keeps Sunday's ID."""
    local = datetime.fromtimestamp(now, ZoneInfo('Europe/Stockholm'))
    sunday = (local - timedelta(days=(local.weekday()+1) % 7)).replace(
        hour=18, minute=0, second=0, microsecond=0)
    if sunday > local:
        sunday -= timedelta(days=7)
    return int(sunday.timestamp())


def prepare(now, output=None):
    """Compute each subscribed topic once. Existing editions are immutable."""
    edition = edition_at(now)
    with connect(DB, readonly=True) as conn:
        checkpoint = conn.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone()
        if not checkpoint or not 0 <= now-checkpoint['value'] <= 36*3600:
            raise RuntimeError('Successful refresh checkpoint is missing or stale')
        topics = query('SELECT DISTINCT topic FROM newsletter_subscriptions WHERE unsubscribed_at IS NULL')
        if output is not None:
            output.mkdir(parents=True, exist_ok=True)
        slugs = topic_registry.slugs(conn)
        for row in topics:
            topic = row['topic']
            stored = query('SELECT html FROM newsletter_issues WHERE topic=? AND edition=?', (topic, edition))
            if stored:
                html = stored[0]['html']
            else:
                current = conn.execute('''SELECT name FROM topics WHERE id=? ''', (topic,)).fetchone()
                if not current:
                    continue
                # Use the previous Sunday boundary, not a fixed 168 hours across DST.
                start = edition_at(edition-1)
                posts = newsletter.candidates(conn, topic, start, edition)
                picks = newsletter.select(posts, current['name'])
                if not picks:
                    continue
                html = newsletter.render_html(current['name'], picks, edition, '{{unsubscribe_url}}', dict(id=topic, slug=slugs[topic]))
                if output is None:
                    query('''INSERT INTO newsletter_issues(topic,edition,prepared_at,source_as_of,subject,html,posts)
                        VALUES(?,?,?,?,?,?,?) ON CONFLICT(topic,edition) DO NOTHING''',
                        (topic, edition, now, checkpoint['value'], 'Hacker Atlas · '+current['name'], html,
                         json.dumps(picks)))
                print(f"{'Previewed' if output is not None else 'Prepared'} topic {topic}: {len(picks)} stories")
            if output is not None:
                (output/f'topic-{topic}.html').write_text(html.replace('{{unsubscribe_url}}',
                    'https://example.invalid/unsubscribe/preview'))
    return edition


def run_weekly(now=None):
    """One locked workflow: prepare first, then send; failures leave it retryable."""
    import requests
    token_path = ROOT / '.wrangler/newsletter-admin-token'
    token = os.environ.get('NEWSLETTER_ADMIN_TOKEN') or (token_path.read_text().strip() if token_path.exists() else '')
    if not token:
        raise RuntimeError('Newsletter delivery admin token is missing')
    config = load_jsonc(ROOT / 'workers/newsletter-test/wrangler.jsonc')
    origin = config['vars']['PUBLIC_URL'].rstrip('/')
    if not origin.startswith('https://'):
        raise RuntimeError('Newsletter Worker URL must use HTTPS')
    with open(str(DB)+'.worker.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        due = edition_at(int(time.time()) if now is None else now)
        with connect(DB, readonly=True) as conn:
            completed = conn.execute(
                "SELECT value FROM maintenance WHERE key='newsletter_completed'").fetchone()
        if completed and completed['value'] >= due:
            print('Weekly edition already completed; skipping', flush=True)
            return dict(sent=0, failures=0, pending=0, skipped=True)
        # After an outage this job may acquire the lock before the daily job.
        # Refresh here while holding it; website deployment is not a dependency.
        ensure_fresh(int(time.time()) if now is None else now)
        now = int(time.time()) if now is None else now
        edition = prepare(now)
        sent = failures = 0
        for _ in range(10000):
            # No automatic HTTP retry: a timeout may follow an accepted delivery.
            try:
                response = requests.post(origin+'/run', headers={'Authorization': 'Bearer '+token},
                    json={'edition': edition, 'limit': 25}, timeout=45)
            except requests.Timeout as error:
                raise RuntimeError('Newsletter delivery timed out; inspect newsletter_deliveries before running again') from error
            response.raise_for_status()
            result = response.json()
            if (result.get('disabled') or any(type(result.get(key)) is not int or result[key] < 0
                    for key in ('sent', 'failures', 'pending'))):
                raise RuntimeError('Newsletter delivery did not complete; inspect Worker delivery state')
            sent += result['sent']; failures += result['failures']
            if failures:
                raise RuntimeError('Newsletter delivery did not complete; inspect newsletter_deliveries')
            if result['pending'] == 0:
                break
            if result.get('done'):
                raise RuntimeError('Newsletter delivery did not complete; inconsistent delivery state')
            if result['sent'] == 0:
                raise RuntimeError('delivery stuck; inspect newsletter_deliveries')
        else:
            raise RuntimeError('Newsletter delivery did not complete; batch iteration limit reached')
        result = dict(sent=sent, failures=failures, pending=0, done=True)
        with connect(DB) as conn:
            conn.execute("INSERT INTO maintenance(key,value) VALUES('newsletter_completed',?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (edition,))
        print(f"Sent {result['sent']} newsletters")
        return result


def ensure_fresh(now):
    """Called under the worker lock; recover a stale corpus before preparation."""
    with connect(DB, readonly=True) as conn:
        checkpoint = conn.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone()
    if checkpoint and 0 <= now-checkpoint['value'] <= 36*3600:
        return
    from hn_sync import open_db
    from refresh import refresh
    print('Refreshing stale corpus before preparing newsletters', flush=True)
    conn = open_db(str(DB))
    try:
        refresh(conn, now=now, discover_topics=False)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='render subscribed topic previews locally without queueing')
    parser.add_argument('--output', type=Path, default=ROOT/'report/newsletter-preview', help='dry-run output directory')
    args = parser.parse_args()
    if args.dry_run:
        prepare(int(time.time()), output=args.output)
    else:
        run_weekly()


if __name__ == '__main__':
    main()
