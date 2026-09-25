"""Optional per-run API ledger and conservative spending ceiling for replays.

Ordinary workers are unaffected unless configure() is explicitly called.
Prices are USD per million tokens, checked against official pricing 2026-09-25.
Unknown/failed responses keep their reservation charged rather than guessing free.
"""
import json
import sqlite3
import time
import threading
from functools import wraps

_ledger = None
_ceiling = None
_lock = threading.RLock()


def locked(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return call
PRICES = {'text-embedding-3-small': (.02, 0), 'gpt-5.6-terra': (2.5, 12),
          'gpt-5.6-luna': (.25, 1.2), 'gpt-6-luna': (.125, .5)}
MAX_OUTPUT = 8192


class BudgetExceeded(RuntimeError):
    pass


@locked
def configure(path, ceiling):
    global _ledger, _ceiling
    if ceiling <= 0:
        raise ValueError('A positive API spending ceiling is required')
    _ledger = sqlite3.connect(path,check_same_thread=False,timeout=60)
    _ledger.execute('''CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY, model TEXT, started REAL, elapsed REAL,
        cost REAL, status TEXT, usage TEXT)''')
    _ledger.commit()
    _ceiling = ceiling


@locked
def reserve(model, inputs, output=0):
    if _ledger is None:
        return None
    rate_in, rate_out = PRICES[model]
    # UTF-8 bytes upper-bound byte-pair token counts; allow chat framing overhead.
    tokens = sum(len(text.encode('utf-8')) for text in inputs) + 100
    reserve_cost = (tokens*rate_in + output*rate_out)/1e6
    # Serialize independent experiment processes as well as local worker threads.
    _ledger.execute('BEGIN IMMEDIATE')
    spent = _ledger.execute('SELECT coalesce(sum(cost),0) FROM calls').fetchone()[0]
    if spent + reserve_cost > _ceiling:
        _ledger.rollback()
        raise BudgetExceeded(f'API ceiling ${_ceiling:.2f}; ledger ${spent:.4f}; '
                             f'next request reserves ${reserve_cost:.4f}')
    cur = _ledger.execute("INSERT INTO calls(model,started,cost,status) VALUES (?,?,?,'reserved')",
                          (model, time.time(), reserve_cost))
    _ledger.commit()
    return cur.lastrowid


@locked
def finish(ident, payload):
    if ident is None:
        return
    model, started = _ledger.execute('SELECT model,started FROM calls WHERE id=?',(ident,)).fetchone()
    usage = payload.get('usage', {})
    if 'prompt_tokens' not in usage:
        return  # retain the conservative reservation
    rate_in, rate_out = PRICES[model]
    # Conservatively price every input token as a cache write (no discounts).
    cost = (usage['prompt_tokens']*rate_in + usage.get('completion_tokens',0)*rate_out)/1e6
    _ledger.execute("UPDATE calls SET elapsed=?,cost=?,status='complete',usage=? WHERE id=?",
                    (time.time()-started,cost,json.dumps(usage),ident))
    _ledger.commit()


def completion_options():
    return {'max_completion_tokens': MAX_OUTPUT} if _ledger is not None else {}
