"""Initialize feed profiles and perform bounded retention during existing deployments.

Use --local DATABASE for a local SQLite check. Without it this writes the configured
newsletter D1; run only as part of an authorized deployment.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from search_sync import Cloudflare

SCHEMA = ROOT / 'migrations/0006_feed_profiles.sql'


def maintenance(sql, now):
    sql(SCHEMA.read_text())
    sql((ROOT / 'migrations/0007_feed_state.sql').read_text())
    # Fixed work budget per run. Indexed expiry and small deletes avoid long locks.
    for table, column, cutoff in (
        ('feed_story_state', 'seen_at', now - 90 * 86400),
        ('feed_events', 'created_at', now - 90 * 86400),
        ('feed_visits', 'created_at', now - 90 * 86400),
        ('feed_login_tokens', 'expires_at', now),
        ('feed_sessions', 'expires_at', now),
        ('feed_rate_limits', 'expires_at', now),
    ):
        for _ in range(8):
            sql(f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {column} < {cutoff} LIMIT 500);')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', help='Local SQLite database, instead of remote D1')
    args = parser.parse_args()
    if args.local:
        with sqlite3.connect(args.local) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            maintenance(conn.executescript, int(time.time()))
    else:
        config = json.loads((ROOT / 'workers/newsletter-test/wrangler.jsonc').read_text())
        resource = config['d1_databases'][0]
        cloud = Cloudflare(dict(account_id=config['account_id'], database_id=resource['database_id']))
        maintenance(cloud.sql, int(time.time()))
    print('Feed profiles ready; bounded 90-day history retention applied.')


if __name__ == '__main__':
    main()
