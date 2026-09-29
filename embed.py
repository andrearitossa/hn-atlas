"""Embed HN post titles and bodies with OpenAI text-embedding-3-small into the `embeddings` table.

Each vector is stored as raw little-endian float32 bytes (DIM floats), exactly as
the API returns it with encoding_format=base64:  numpy.frombuffer(vec, "<f4").

Called by refresh.py; unchanged vectors are reused.
"""
import base64
import html
import hashlib
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

MODEL, DIM = "text-embedding-3-small", 512
SINCE = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
BATCH = 32  # At most 256k input bytes, including batches of long post bodies.
MAX_RETRIES = 20

import config

SCHEMA = """CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY REFERENCES stories(id),
    model TEXT NOT NULL,
    vec BLOB NOT NULL
)"""


def to_text(title, url, text, version=2) -> str:
    """What we embed: title, the linked site, and the start of any self-post text."""
    if version == 2:
        from routing import subject_text
        return subject_text(title, url, text)
    if version != 1:
        raise ValueError(f"Unsupported embedding input version: {version}")
    parts = [title or ""]
    if url:
        parts.append(f"({urlparse(url).netloc.removeprefix('www.')})")
    if text:
        parts.append(re.sub(r"<[^>]+>", " ", html.unescape(text))[:1000])
    return " ".join(parts)


def embed_batch(texts: list[str]) -> list[bytes]:
    """One API call; retries with backoff on rate limits (429) and server errors."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json={"model": MODEL, "input": texts, "dimensions": DIM, "encoding_format": "base64"},
                timeout=120,
            )
        except (requests.ConnectionError, requests.Timeout) as error:
            if attempt + 1 >= MAX_RETRIES:
                raise
            print(f"Transient API {type(error).__name__}; retry {attempt+1}", flush=True)
            time.sleep(min(30, 2 ** attempt))
            continue
        if r.status_code != 429 and r.status_code < 500:
            r.raise_for_status()
            payload = r.json()
            data = sorted(payload["data"], key=lambda d: d["index"])
            return [base64.b64decode(d["embedding"]) for d in data]
        if attempt >= 5:
            print(f"  still retrying (attempt {attempt + 1}, HTTP {r.status_code})", flush=True)
        time.sleep(min(60, 2 ** attempt))
    r.raise_for_status()


def embed_pending(conn, version=2, limit=None, since=SINCE, until=None) -> int:
    """Embed live articles in [since, until), using bounded memory."""
    if version not in (1, 2) or (limit is not None and limit < 1):
        raise ValueError('Invalid embedding version or limit')
    conn.execute(SCHEMA)
    conn.execute('CREATE TABLE IF NOT EXISTS dirty_stories (id INTEGER PRIMARY KEY)')
    conn.execute('''CREATE TABLE IF NOT EXISTS embedding_errors (
        id INTEGER PRIMARY KEY, reason TEXT, input_version INTEGER, updated_at INTEGER)''')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(embeddings)')}
    if 'input_version' not in columns:
        conn.execute('ALTER TABLE embeddings ADD COLUMN input_version INTEGER NOT NULL DEFAULT 1')
    if 'input_hash' not in columns:
        conn.execute('ALTER TABLE embeddings ADD COLUMN input_hash TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS embedding_input ON embeddings(model,input_version,input_hash)')
    conn.create_function('subject_hash', 3, lambda title, url, body:
                         hashlib.sha256(to_text(title, url, body, version=version).encode()).hexdigest())
    rows = conn.execute(
        """SELECT s.id, s.title, s.url, s.text, e.id FROM stories s
           LEFT JOIN embeddings e ON e.id = s.id
           WHERE (e.id IS NULL OR e.input_version != ? OR e.model != ?
                  OR (e.input_hash IS NOT NULL AND e.input_hash != subject_hash(s.title,s.url,s.text)))
             AND s.id IN (SELECT id FROM stories WHERE time >= ? UNION SELECT id FROM dirty_stories) AND s.dead = 0 AND s.deleted = 0
             AND s.title IS NOT NULL AND (? IS NULL OR s.time < ?)
             AND s.id NOT IN (SELECT id FROM embedding_errors WHERE input_version=?)
           ORDER BY s.id LIMIT ?""",
        (version, MODEL, since, until, until, version, limit if limit is not None else -1),
    )
    done = generated_count = reused_count = 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Cache identical normalized inputs, including repeated submissions. Bounded
    # batches keep memory use predictable; existing v1 embeddings stay compatible.
    while batch := rows.fetchmany(BATCH):
        groups = {}
        for row in batch:
            text = to_text(*row[1:4], version=version)
            if not text.strip():
                conn.execute('DELETE FROM embeddings WHERE id=?', (row[0],))
                conn.execute('INSERT OR REPLACE INTO embedding_errors VALUES (?,?,?,?)',
                             (row[0],'empty_subject',version,int(time.time())))
                continue
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            groups.setdefault(fingerprint, {'text': text, 'ids': []})['ids'].append(row[0])
        pending = []
        vectors = {}
        for fingerprint, group in groups.items():
            cached = conn.execute('SELECT vec FROM embeddings WHERE model=? AND input_version=? AND input_hash=? LIMIT 1',
                                  (MODEL, version, fingerprint)).fetchone()
            if cached:
                if len(cached[0]) != DIM * 4:
                    raise ValueError('Cached embedding has unexpected dimensions')
                vectors[fingerprint] = cached[0]
                reused_count += len(group['ids'])
            else:
                pending.append(fingerprint)
        if pending:
            generated = embed_batch([groups[k]['text'] for k in pending])
            if len(generated) != len(pending) or any(len(v) != DIM * 4 for v in generated):
                raise ValueError('Embedding response has unexpected count or dimensions')
            vectors.update(zip(pending, generated))
            generated_count += len(pending)
        # Changed input must be reviewed again; copying a missing vector alone
        # does not invalidate existing curated assignments.
        changed = [(row[0],) for row in batch if row[4] is not None]
        for table in ('story_topics', 'classification_queue'):
            if table in tables:
                conn.executemany(f'DELETE FROM {table} WHERE id=?', changed)
        conn.executemany('INSERT OR REPLACE INTO embeddings(id,model,vec,input_version,input_hash) VALUES (?,?,?,?,?)',
                         [(i, MODEL, vectors[k], version, k) for k, group in groups.items() for i in group['ids']])
        conn.executemany('DELETE FROM dirty_stories WHERE id=?', [(row[0],) for row in batch])
        conn.commit()
        done += sum(len(group['ids']) for group in groups.values())
        print(f'  vectors {done:,}: {reused_count:,} reused, {generated_count:,} generated', flush=True)
    return done
