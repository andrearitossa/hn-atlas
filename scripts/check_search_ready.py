"""Prevent publishing the server Search UI before its initial sync completes."""
from pathlib import Path
import sqlite3
ROOT = Path(__file__).resolve().parents[1]
ledger = ROOT/'data/search-sync.sqlite'
if not ledger.exists():
    raise SystemExit('Search has not been synced. Run search_sync.py before deploying.')
with sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro', uri=True) as conn:
    row = conn.execute("SELECT value FROM settings WHERE key='as_of'").fetchone()
    layout = conn.execute("SELECT value FROM settings WHERE key='layout'").fetchone()
if not row or not layout or layout[0] != 'topic-metadata-v1':
    raise SystemExit('Initial Search sync is incomplete; the live site has not been replaced.')
print('Search initial sync completed; deployment may proceed.')
