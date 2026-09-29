"""Show freshness and pending work in the canonical database."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DB
from database import connect


def status(path=DB):
    with connect(path, readonly=True) as conn:
        return {
            'database': str(path),
            'stories': conn.execute('SELECT count(*) FROM stories').fetchone()[0],
            'latest_story': conn.execute("SELECT datetime(max(time),'unixepoch') FROM stories").fetchone()[0],
            'last_success': dict(conn.execute("SELECT key,datetime(value,'unixepoch') FROM maintenance")),
            'sync': dict(conn.execute('SELECT key,value FROM sync_state')),
            'waiting': dict(conn.execute('SELECT reason,count(*) FROM classification_queue GROUP BY reason')),
        }


if __name__ == '__main__':
    print(json.dumps(status(), indent=2))
