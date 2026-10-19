"""Explicitly generate missing archive embeddings (uses the paid embedding API)."""
import argparse
from config import DB
from database import connect
from embed import embed_pending

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=1000, help='Maximum stories per invocation (default: 1000)')
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    # Share the same writer lock as the refresh pipeline.
    import fcntl
    with open(str(DB) + '.worker.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with connect(DB) as conn:
            embed_pending(conn, since=0, limit=args.limit)
