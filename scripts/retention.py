"""Expire private feed state and search history with a fixed daily work budget."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloudflare import Cloudflare, newsletter_config


def expire(sql, now, search=False):
    tables = [('searches', 'created_at', now - 90 * 86400)] if search else [
        ('feed_story_state', 'seen_at', now - 90 * 86400),
        ('feed_login_tokens', 'expires_at', now),
        ('feed_sessions', 'expires_at', now),
        ('feed_rate_limits', 'expires_at', now),
    ]
    for table, column, cutoff in tables:
        for _ in range(8):
            sql(f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {column} < {cutoff} LIMIT 500);')


def main():
    now = int(time.time())
    expire(Cloudflare(newsletter_config()).sql, now)
    path = ROOT / 'search-cloudflare.json'
    if path.exists():
        expire(Cloudflare(json.loads(path.read_text())).sql, now, search=True)
    print('Bounded daily retention complete.')


if __name__ == '__main__':
    main()
