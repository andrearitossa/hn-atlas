#!/usr/bin/env python3
"""Incrementally sync HN articles to SQLite; retain the historical archive."""
import sqlite3
import threading
import time

import requests

BASE_URL = "https://hacker-news.firebaseio.com/v0"
ARTICLE_TYPES = {"story", "job", "poll"}
FIELDS = 'id type by time title url text score descendants dead deleted fetched_at'.split()
UPSERT = (f"INSERT INTO stories ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)}) "
          "ON CONFLICT(id) DO UPDATE SET " + ','.join(f'{f}=excluded.{f}' for f in FIELDS[1:]))

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY, type TEXT, by TEXT, time INTEGER,
    title TEXT, url TEXT, text TEXT, score INTEGER, descendants INTEGER,
    dead INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
    fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stories_time ON stories(time);
CREATE INDEX IF NOT EXISTS idx_stories_type ON stories(type);
CREATE INDEX IF NOT EXISTS idx_stories_score ON stories(score);

CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dirty_stories (id INTEGER PRIMARY KEY);
"""

_local = threading.local()


def get_json(path: str, retries: int = 5):
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    for attempt in range(retries):
        try:
            resp = _local.session.get(f"{BASE_URL}/{path}.json", timeout=15)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)


def fetch_item(item_id: int):
    return get_json(f"item/{item_id}")


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA cache_size=-65536')
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def save_articles(conn: sqlite3.Connection, items) -> int:
    now = int(time.time())
    items = [it for it in items if it]  # Finish all fetches before writing a batch.
    # Tombstones can omit type/title; still hide the stored article.
    conn.executemany(
        'UPDATE stories SET dead=?,deleted=?,fetched_at=? WHERE id=?',
        [(int(bool(it.get('dead'))), int(bool(it.get('deleted'))), now, it['id'])
         for it in items if it.get('dead') or it.get('deleted')],
    )
    rows = [(it['id'], *(it.get(f) for f in FIELDS[1:9]),
             int(bool(it.get('dead'))), int(bool(it.get('deleted'))), now)
            for it in items if it.get('type') in ARTICLE_TYPES]
    # Content changes invalidate classifications; score changes do not.
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
            for table in ('embeddings','embedding_errors','story_topics','classification_queue'):
                if table in tables:
                    conn.execute(f'DELETE FROM {table} WHERE id=?', (row[0],))
            conn.execute('INSERT OR IGNORE INTO dirty_stories VALUES (?)', (row[0],))
    conn.executemany(UPSERT, rows)
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


def fetch_batches(conn, pool, ids, chunk):
    """Commit complete batches; fetch failures leave earlier batches intact."""
    total = 0
    for start in range(0, len(ids), chunk):
        batch = ids[start:start + chunk]
        total += save_articles(conn, pool.map(fetch_item, batch))
        conn.commit()
        print(f'  fetched {start + len(batch):,}/{len(ids):,} ids; {total:,} articles saved', flush=True)
    return total


def catch_up(conn, pool, chunk: int = 2000) -> int:
    """Fetch every item posted since the previous run. Returns the new max id."""
    max_id = get_state(conn, "high")
    if max_id is None:
        max_id = conn.execute('SELECT max(id) FROM stories').fetchone()[0]
    current_max = get_json("maxitem")
    if max_id is None:
        max_id = max(0, current_max - chunk)
    # Persist the starting checkpoint so partial first runs cannot create gaps.
    if get_state(conn, 'high') is None:
        set_state(conn, 'high', max_id)
        conn.commit()
    fetch_batches(conn, pool, range(current_max, max_id, -1), chunk)
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
    return fetch_batches(conn, pool, ids, 1000)
