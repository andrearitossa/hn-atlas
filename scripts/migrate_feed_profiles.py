"""Apply feed profile schemas during authorized deployments.

Use --local DATABASE for a local SQLite check. Without it this writes the configured
newsletter D1; run only as part of an authorized deployment.
"""
import argparse
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloudflare import Cloudflare, newsletter_config


def apply_schema(sql):
    for name in ('0006_feed_profiles', '0007_feed_state', '0008_drop_feed_history', '0009_feed_topics_single_source'):
        sql((ROOT / 'migrations' / (name + '.sql')).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', help='Local SQLite database, instead of remote D1')
    args = parser.parse_args()
    if args.local:
        with sqlite3.connect(args.local) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            apply_schema(conn.executescript)
    else:
        cloud = Cloudflare(newsletter_config())
        apply_schema(cloud.sql)
    print('Feed profile schemas ready.')


if __name__ == '__main__':
    main()
