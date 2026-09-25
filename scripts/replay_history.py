#!/usr/bin/env python3
"""Isolated, resumable pre-2020 build followed by weekly historical ingestion.

Prepare is read-only with respect to the source. Run never fetches HN or sends mail.
The snapshot's scores/edits are retrospective, not historical event-time values.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_usage
import weekly
import embed
import hn_sync
import production
import topics

SPLIT = 1577836800  # 2020-01-01 00:00:00 UTC
DAY = 86400


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


def prepare(source, folder):
    manifest = folder/'manifest.json'
    if manifest.exists():
        existing = json.loads(manifest.read_text())
        if existing['source'] != str(source.resolve()):
            raise ValueError('This run belongs to a different source')
        return existing
    snapshot = folder/'snapshot.db'
    if snapshot.exists():
        raise ValueError('Incomplete snapshot exists; inspect it before retrying prepare')
    conn = hn_sync.open_db(str(snapshot))
    try:
        conn.execute('ATTACH DATABASE ? AS original', (source.resolve().as_uri()+'?mode=ro',))
        # One SELECT/transaction gives a consistent public-story snapshot, excluding
        # all accounts, subscriptions, classifications, and existing model state.
        conn.execute('''INSERT INTO stories SELECT * FROM original.stories
            WHERE dead=0 AND deleted=0 AND title IS NOT NULL AND time IS NOT NULL''')
        conn.commit()
        before, after, oldest, latest = conn.execute('''SELECT sum(time<?),sum(time>=?),min(time),max(time)
            FROM stories''',(SPLIT,SPLIT)).fetchone()
        if not before or not after:
            raise ValueError('Both sides of the 2020 split must contain stories')
        result = dict(source=str(source.resolve()), split=SPLIT, end=latest+1,
                      initial_stories=before, streamed_stories=after, oldest=oldest,
                      prepared_at=int(time.time()),
                      caveat='Snapshot scores, edits, deletion state, and modern model knowledge are retrospective.')
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        save(manifest,result)
        return result
    finally:
        conn.close()


def ingest(conn, lower, upper):
    conn.execute('''INSERT OR IGNORE INTO stories SELECT * FROM archive.stories
        WHERE time>=? AND time<?''',(lower,upper))
    conn.commit()


def run(folder, ceiling, stop=None):
    manifest = json.loads((folder/'manifest.json').read_text())
    end = manifest['end'] if stop is None else min(stop, manifest['end'])
    if end <= SPLIT:
        raise ValueError('Replay end must be after 2020-01-01')
    db, artifact = folder/'replay.db', folder/'topics.npz'
    production.MODEL_PATH = str(artifact.resolve())
    api_usage.configure(str(folder/'api-usage.db'), ceiling)
    checkpoint = folder/'progress.json'
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else dict(stage='initial', through=SPLIT)
    conn = hn_sync.open_db(str(db))
    conn.execute('ATTACH DATABASE ? AS archive',((folder/'snapshot.db').resolve().as_uri()+'?mode=ro',))
    try:
        if state['stage'] == 'initial':
            ingest(conn,0,SPLIT)
            embed.embed_pending(conn,version=2,since=0,until=SPLIT)
            if not artifact.exists():
                topics.main(str(db), str(artifact), now=SPLIT-1)
            production.setup(conn,now=SPLIT-1)
            # Preserve a lightweight immutable record of the initial taxonomy.
            save(folder/'initial-topics.json',[dict(zip(('id','name','description'),r))
                 for r in conn.execute('SELECT id,name,description FROM topics ORDER BY id')])
            state.update(stage='stream',through=SPLIT)
            save(checkpoint,state)
        while state['through'] < end:
            lower = state['through']
            upper = min(lower+7*DAY,end)
            now = upper-1
            print(f'Replaying {stamp(lower)} → {stamp(upper)}',flush=True)
            # Only arrived stories exist in the working DB: initial fitting,
            # reviewers, canonical caches, and discovery cannot see future rows.
            ingest(conn,lower,upper)
            embed.embed_pending(conn,version=2,since=lower,until=upper)
            weekly.classify_pending(conn,now=now)
            events = weekly.maintain(conn,now=now)
            record = dict(through=upper,date=stamp(upper),events=events,
                          topics=conn.execute("SELECT count(*) FROM topic_registry WHERE status='active'").fetchone()[0],
                          assigned=conn.execute('SELECT count(*) FROM story_topics').fetchone()[0],
                          queued=conn.execute('SELECT count(*) FROM classification_queue').fetchone()[0])
            with (folder/'days.jsonl').open('a') as out:
                out.write(json.dumps(record)+'\n')
            state.update(stage='stream',through=upper,last_day=record)
            save(checkpoint,state)
        state['stage'] = 'complete' if end == manifest['end'] else 'stream'
        save(checkpoint,state)
        print(json.dumps(state,indent=2),flush=True)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run'])
    parser.add_argument('--source',type=Path,default=Path('data/hackernews.db'))
    parser.add_argument('--out',type=Path,default=Path('data/replay-2020'))
    parser.add_argument('--max-usd',type=float)
    parser.add_argument('--until',help='Exclusive UTC date; defaults to the source snapshot end')
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    if args.source.resolve() in {(args.out/n).resolve() for n in ('snapshot.db','replay.db')}:
        parser.error('Source must be outside the replay artifacts')
    with (args.out/'run.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.action == 'prepare':
            print(json.dumps(prepare(args.source,args.out),indent=2))
        else:
            if args.max_usd is None:
                parser.error('run requires --max-usd for the cumulative API ceiling')
            stop = int(datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc).timestamp()) if args.until else None
            run(args.out,args.max_usd,stop)


if __name__ == '__main__':
    main()
