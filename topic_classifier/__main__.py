"""File already-embedded new stories with the stored model; optionally export the site."""
import argparse
import fcntl
import json
import time

from config import DB
import hn_sync
import topics
from topic_classifier import classify, load


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default=str(DB))
    parser.add_argument('--publish',metavar='DIRECTORY')
    args=parser.parse_args()
    with open(args.db+'.worker.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        conn=hn_sync.open_db(args.db)
        try:
            conn.executescript(topics.TOPIC_SCHEMA)
            now=int(time.time())
            with conn:
                filed=classify(conn,now)
            print(json.dumps(dict(model=load().version,filed=filed,
                memberships=conn.execute('SELECT count(*) FROM story_topic_labels WHERE assigned_at=?',(now,)).fetchone()[0],
                capped_below_target=conn.execute('SELECT count(*) FROM story_topic_labels WHERE assigned_at=? AND rank=1 AND target_reached=0',(now,)).fetchone()[0])),flush=True)
        finally:
            conn.close()
    if args.publish:
        from publish import publish
        print(f'Published {publish(args.db,args.publish)}',flush=True)


if __name__=='__main__':
    main()
