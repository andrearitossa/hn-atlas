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
        candidates=[dict(id=r[0],status=r[1],support=r[2],first_seen=r[3],last_seen=r[4],topic=r[5])
                    for r in conn.execute('SELECT id,status,support,first_seen,last_seen,topic FROM topic_candidates ORDER BY last_seen DESC')] if 'topic_candidates' in tables else []
        return dict(queue=queue,candidates=candidates)
    finally:conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default=os.getenv('HN_DB','data/hackernews.db'))
    print(json.dumps(status(parser.parse_args().db),indent=2))
