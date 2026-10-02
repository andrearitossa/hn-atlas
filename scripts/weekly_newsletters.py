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
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DB
import newsletter
from database import connect

ACCOUNT = 'c2e5e5e106e0b8c69e797dd75d0d229d'
DATABASE = 'b25ea9c4-25e9-4547-b753-292c752e0eef'


def query(sql, params=()):
    import requests
    token = os.environ.get('CLOUDFLARE_API_TOKEN')
    if not token:
        # Wrangler refreshes expired OAuth credentials. Reading its token file
        # directly works interactively but fails on a later unattended run.
        credentials = subprocess.run(
            [str(ROOT/'node_modules/.bin/wrangler'), 'auth', 'token', '--json'],
            cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        if credentials.returncode:
            raise RuntimeError('Cloudflare authentication failed; run wrangler login or configure CLOUDFLARE_API_TOKEN')
        token = json.loads(credentials.stdout).get('token')
        if not token:
            raise RuntimeError('Cloudflare authentication did not return a token')
    response = requests.post(f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/d1/database/{DATABASE}/query',
        headers={'Authorization': f'Bearer {token}'}, json={'sql': sql, 'params': list(params)}, timeout=60)
    data = response.json()
    if not response.ok or not data.get('success') or not all(item.get('success') for item in data.get('result', [])):
        raise RuntimeError(f'Cloudflare D1 query failed: {data.get("errors", data)}')
    return data['result'][0]['results']


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
                html = newsletter.render_html(current['name'], picks, edition, '{{unsubscribe_url}}', topic)
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
    config = json.loads((ROOT / 'workers/newsletter-test/wrangler.jsonc').read_text())
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
        # No automatic HTTP retry: a timeout may follow an accepted delivery.
        response = requests.post(origin+'/run', headers={'Authorization': 'Bearer '+token}, json={'edition': edition}, timeout=120)
        response.raise_for_status()
        result = response.json()
        if (result.get('disabled') or result.get('failures') != 0 or result.get('pending') != 0
                or type(result.get('sent')) is not int):
            raise RuntimeError('Newsletter delivery did not complete; inspect Worker delivery state')
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
