"""Inspect pending classification and topic discovery without modifying the DB."""
import argparse
import json
import os
from pathlib import Path
import sqlite3


def status(path):
    conn=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        queue=dict(conn.execute('SELECT reason,count(*) FROM classification_queue GROUP BY reason')) if 'classification_queue' in tables else {}
        checkpoints=dict(conn.execute('SELECT key,value FROM maintenance')) if 'maintenance' in tables else {}
        latest=conn.execute("SELECT at,details FROM topic_changes WHERE kind='review' ORDER BY at DESC LIMIT 1").fetchone() if 'topic_changes' in tables else None
        return dict(unlisted_stories=queue,checkpoints=checkpoints,
                    latest_map_review=dict(at=latest[0],**json.loads(latest[1])) if latest else None)

    finally:conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default=os.getenv('HN_DB','data/hackernews.db'))
    print(json.dumps(status(parser.parse_args().db),indent=2))
