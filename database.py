"""Short-lived SQLite connections with explicit transaction and cleanup boundaries."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path, *, readonly=False):
    target = Path(path).resolve().as_uri() + '?mode=ro' if readonly else path
    conn = sqlite3.connect(target, uri=readonly, timeout=30)
    conn.execute('PRAGMA cache_size=-65536')  # 64 MiB for the multi-million-story corpus.
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()
