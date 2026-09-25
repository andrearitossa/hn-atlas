#!/usr/bin/env python3
"""Fetch Hacker News articles into a local SQLite database.

Uses the official HN Firebase API (https://github.com/HackerNews/API). Comments are skipped;
only stories, jobs and polls are stored.

Use hn_import_dump.py for history. This module updates from the official API.
"""

import argparse
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = "https://hacker-news.firebaseio.com/v0"
FEEDS = ["topstories", "newstories", "beststories", "askstories", "showstories", "jobstories"]
ARTICLE_TYPES = {"story", "job", "poll"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id          INTEGER PRIMARY KEY,
    type        TEXT,
    by          TEXT,
    time        INTEGER,
    title       TEXT,
    url         TEXT,
    text        TEXT,
    score       INTEGER,
    descendants INTEGER,
    dead        INTEGER NOT NULL DEFAULT 0,
    deleted     INTEGER NOT NULL DEFAULT 0,
    fetched_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stories_time ON stories(time);
CREATE INDEX IF NOT EXISTS idx_stories_type ON stories(type);
CREATE INDEX IF NOT EXISTS idx_stories_score ON stories(score);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def get_json(path: str, retries: int = 5):
    for attempt in range(retries):
        try:
            resp = _session().get(f"{BASE_URL}/{path}.json", timeout=15)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)


def fetch_item(item_id: int):
    # A transient failure must stop the chunk. Silently skipping it makes a
    # permanent hole when the high-water mark advances.
    return get_json(f"item/{item_id}")


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def save_articles(conn: sqlite3.Connection, items) -> int:
    now = int(time.time())
    items = list(items)
    # HN deletion tombstones can omit type/title. Preserve the stored article
    # identity while hiding it everywhere that reads live stories.
    conn.executemany(
        'UPDATE stories SET dead=?,deleted=?,fetched_at=? WHERE id=?',
        [(int(bool(it.get('dead'))), int(bool(it.get('deleted'))), now, it['id'])
         for it in items if it and (it.get('dead') or it.get('deleted'))],
    )
    rows = [
        (
            it["id"], it.get("type"), it.get("by"), it.get("time"), it.get("title"),
            it.get("url"), it.get("text"), it.get("score"), it.get("descendants"),
            int(bool(it.get("dead"))), int(bool(it.get("deleted"))), now,
        )
        for it in items
        if it and it.get("type") in ARTICLE_TYPES
    ]
    # Scores change frequently; content changes must invalidate the old vector
    # and assignment so refreshed headlines cannot retain stale classifications.
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'embeddings' in tables:
        versioned = 'input_version' in {r[1] for r in conn.execute('PRAGMA table_info(embeddings)')}
        for row in rows:
            old = conn.execute('SELECT title,url,text FROM stories WHERE id=?', (row[0],)).fetchone()
            if old is None or tuple(old) == (row[4], row[5], row[6]) or row[9] or row[10]:
                continue
            version = conn.execute('SELECT input_version FROM embeddings WHERE id=?', (row[0],)).fetchone() if versioned else None
            if version and version[0] == 2:
                from routing import metadata, subject_text
                new = (row[4], row[5], row[6])
                if subject_text(*old) == subject_text(*new) and metadata(*old)[0] == metadata(*new)[0]:
                    continue
            if 'story_metadata' in tables and 'article_decisions' in tables:
                conn.execute('DELETE FROM article_decisions WHERE canonical IN '
                             '(SELECT canonical FROM story_metadata WHERE id=?)', (row[0],))
            for table in ('embeddings','embedding_errors','story_topics','classification_queue','story_metadata'):
                if table in tables:
                    conn.execute(f'DELETE FROM {table} WHERE id=?', (row[0],))
    conn.executemany(
        """INSERT INTO stories (id, type, by, time, title, url, text, score,
                                descendants, dead, deleted, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               type=excluded.type, by=excluded.by, time=excluded.time,
               title=excluded.title, url=excluded.url, text=excluded.text,
               score=excluded.score, descendants=excluded.descendants,
               dead=excluded.dead, deleted=excluded.deleted,
               fetched_at=excluded.fetched_at""",
        rows,
    )
    return len(rows)


def get_state(conn, key):
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn, key, value):
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def sync_feeds(conn, pool):
    ids = set()
    for feed in FEEDS:
        feed_ids = get_json(feed) or []
        print(f"{feed}: {len(feed_ids)} ids")
        ids.update(feed_ids)
    print(f"Fetching {len(ids)} unique items...")
    saved = save_articles(conn, pool.map(fetch_item, sorted(ids)))
    conn.commit()
    print(f"Saved {saved} articles.")


def fetch_range(conn, pool, hi: int, lo: int, chunk: int):
    """Fetch ids hi..lo (inclusive, descending) in chunks, committing each one."""
    total = 0
    start = time.time()
    for top in range(hi, lo - 1, -chunk):
        bottom = max(top - chunk + 1, lo)
        total += save_articles(conn, pool.map(fetch_item, range(top, bottom - 1, -1)))
        conn.commit()
        done = hi - bottom + 1
        rate = done / (time.time() - start)
        print(f"  ids {bottom:>9}..{top:<9} | {total} articles saved | {rate:,.0f} items/s")
    return total


def catch_up(conn, pool, chunk: int = 2000) -> int:
    """Fetch every item posted since the previous run. Returns the new max id."""
    max_id = get_state(conn, "high")
    if max_id is None:
        max_id = conn.execute('SELECT max(id) FROM stories').fetchone()[0]
    current_max = get_json("maxitem")
    if max_id is None:
        max_id = max(0, current_max - chunk)
    if max_id is not None and current_max > max_id:
        print(f"Catching up new items {max_id + 1}..{current_max}")
        fetch_range(conn, pool, current_max, max_id + 1, chunk)
    set_state(conn, "high", current_max)
    conn.commit()
    return current_max


def refresh_recent(conn, pool, days: int = 14, now=None, fetched_before=None) -> int:
    """Re-fetch stories from the last few days so their scores and comment counts are current."""
    now = int(time.time()) if now is None else now
    since = now - days * 86400
    ids = [r[0] for r in conn.execute("SELECT id FROM stories WHERE time >= ? AND time<=? "
        "AND (? IS NULL OR fetched_at<?)", (since,now,fetched_before,fetched_before))]
    print(f"Refreshing {len(ids)} stories from the last {days} days")
    saved = save_articles(conn, pool.map(fetch_item, ids))
    conn.commit()
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="data/hackernews.db", help="SQLite file (default: data/hackernews.db)")
    parser.add_argument("--workers", type=int, default=32, help="concurrent requests (default: 32)")
    args = parser.parse_args()

    conn = open_db(args.db)
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            sync_feeds(conn, pool)
    except KeyboardInterrupt:
        conn.commit()
        print("\nInterrupted; progress saved. Re-run to resume.")
    finally:
        count = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        print(f"Database {args.db}: {count} articles total.")
        conn.close()


if __name__ == "__main__":
    main()
