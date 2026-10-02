"""Prepare and send Daily selection after the daily publication succeeds."""
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config
from database import connect
import daily_selection
from scripts.weekly_newsletters import query

SCHEMA = '''CREATE TABLE IF NOT EXISTS daily_selections (
 edition TEXT PRIMARY KEY, prepared_at INTEGER NOT NULL, subject TEXT NOT NULL,
 html TEXT NOT NULL, posts TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'prepared',
 message_id TEXT, error TEXT)'''


def run(dry_run=False):
    import requests
    with open(ROOT/'data/daily-selection.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = int(time.time())
        edition = datetime.fromtimestamp(now, ZoneInfo('Europe/Stockholm')).strftime('%Y-%m-%d')
        if not dry_run:
            if (ROOT/'data/update-site-success.date').read_text().strip() != edition:
                raise RuntimeError('Daily publication has not succeeded today')
            query(SCHEMA)
            stored = query('SELECT state FROM daily_selections WHERE edition=?', (edition,))
            if stored and stored[0]['state'] == 'sent':
                print('Daily selection already sent')
                return
            if stored and stored[0]['state'] in ('sending', 'failed'):
                raise RuntimeError('Delivery requires inspection before retry; avoiding a duplicate email')
        else:
            stored = []
        if not stored:
            with connect(config.DB, readonly=True) as conn:
                checkpoint = conn.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone()
                if not checkpoint or not 0 <= now-checkpoint['value'] <= daily_selection.WINDOW:
                    raise RuntimeError('Story refresh is missing or stale')
                posts = daily_selection.prepare(conn, now)
            html = daily_selection.render(posts, now)
            output = ROOT/'report/daily-selection'
            output.mkdir(parents=True, exist_ok=True)
            (output/f'{edition}.html').write_text(html)
            (output/f'{edition}.json').write_text(json.dumps(posts, ensure_ascii=False, indent=2))
            if dry_run:
                print(f'Preview: {output/edition}.html')
                return
            query('INSERT INTO daily_selections(edition,prepared_at,subject,html,posts) VALUES(?,?,?,?,?)',
                  (edition, now, f'Hacker Atlas · Daily selection · {edition}', html, json.dumps(posts)))
        token_path = ROOT/'.wrangler/newsletter-admin-token'
        token = os.environ.get('NEWSLETTER_ADMIN_TOKEN') or (token_path.read_text().strip() if token_path.exists() else '')
        if not token:
            raise RuntimeError('Newsletter admin token missing')
        worker = json.loads((ROOT/'workers/newsletter-test/wrangler.jsonc').read_text())
        response = requests.post(worker['vars']['PUBLIC_URL'].rstrip('/')+'/daily-selection',
            headers={'Authorization': 'Bearer '+token}, json={'edition': edition}, timeout=120)
        response.raise_for_status()
        if response.json().get('state') != 'sent':
            raise RuntimeError('Daily selection delivery did not complete')
        print(f'Daily selection sent to {daily_selection.RECIPIENT}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    run(parser.parse_args().dry_run)
