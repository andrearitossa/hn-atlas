"""Embed article titles with OpenAI text-embedding-3-small into the `embeddings` table.

Each vector is stored as raw little-endian float32 bytes (DIM floats), exactly as
the API returns it with encoding_format=base64:  numpy.frombuffer(vec, "<f4").

    .venv/bin/python embed.py      # embed everything pending (key from .env or env)
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
import api_usage

MODEL, DIM = "text-embedding-3-small", 512
SINCE = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
BATCH, WORKERS = 1000, 4   # 1000 titles ~ 30k tokens; the account allows 1M tokens/min
MAX_RETRIES = 20

# Load KEY=value lines from .env (if present) without overriding the real environment.
if os.path.exists(".env"):
    for line in open(".env"):
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#"):
            os.environ.setdefault(k, v.strip().strip("'\""))

SCHEMA = """CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY REFERENCES stories(id),
    model TEXT NOT NULL,
    vec BLOB NOT NULL
)"""


def to_text(title, url, text, version=1) -> str:
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
        ticket = api_usage.reserve(MODEL, texts)
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
            api_usage.finish(ticket, payload)
            data = sorted(payload["data"], key=lambda d: d["index"])
            return [base64.b64decode(d["embedding"]) for d in data]
        if attempt >= 5:
            print(f"  still retrying (attempt {attempt + 1}, HTTP {r.status_code})", flush=True)
        time.sleep(min(60, 2 ** attempt))
    r.raise_for_status()


def embed_pending(conn, version=1, limit=None, since=SINCE, until=None) -> int:
    """Embed live articles in [since, until), using bounded memory."""
    if version not in (1, 2) or (limit is not None and limit < 1):
        raise ValueError('Invalid embedding version or limit')
    conn.execute(SCHEMA)
    conn.execute('''CREATE TABLE IF NOT EXISTS embedding_errors (
        id INTEGER PRIMARY KEY, reason TEXT, input_version INTEGER, updated_at INTEGER)''')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(embeddings)')}
    if 'input_version' not in columns:
        conn.execute('ALTER TABLE embeddings ADD COLUMN input_version INTEGER NOT NULL DEFAULT 1')
    if 'input_hash' not in columns:
        conn.execute('ALTER TABLE embeddings ADD COLUMN input_hash TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS embedding_input ON embeddings(model,input_version,input_hash)')
    rows = conn.execute(
        """SELECT s.id, s.title, s.url, s.text FROM stories s
           LEFT JOIN embeddings e ON e.id = s.id
           WHERE (e.id IS NULL OR e.input_version != ?) AND s.time >= ? AND s.dead = 0 AND s.deleted = 0
             AND s.title IS NOT NULL AND (? IS NULL OR s.time < ?)
             AND s.id NOT IN (SELECT id FROM embedding_errors WHERE input_version=?)
           ORDER BY s.id LIMIT ?""",
        (version, since, until, until, version, limit if limit is not None else -1),
    )
    done = 0
    # Cache identical normalized inputs, including repeated submissions. Bounded
    # batches keep memory use predictable; existing v1 embeddings stay compatible.
    while batch := rows.fetchmany(BATCH):
        groups = {}
        for row in batch:
            text = to_text(*row[1:], version=version)
            if not text.strip():
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
                vectors[fingerprint] = cached[0]
            else:
                pending.append(fingerprint)
        if pending:
            generated = embed_batch([groups[k]['text'] for k in pending])
            if len(generated) != len(pending) or any(len(v) != DIM * 4 for v in generated):
                raise ValueError('Embedding response has unexpected count or dimensions')
            vectors.update(zip(pending, generated))
        conn.executemany('INSERT OR REPLACE INTO embeddings(id,model,vec,input_version,input_hash) VALUES (?,?,?,?,?)',
                         [(i, MODEL, vectors[k], version, k) for k, group in groups.items() for i in group['ids']])
        conn.commit()
        done += sum(len(group['ids']) for group in groups.values())
        print(f'  embedded {done:,}', flush=True)
    return done


if __name__ == "__main__":
    from hn_sync import open_db
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.getenv('HN_DB', 'data/hackernews.db'))
    parser.add_argument('--version', type=int, choices=(1, 2), default=1)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    conn = open_db(args.db)
    try:
        if args.version == 2 and conn.execute("SELECT 1 FROM sqlite_master WHERE name='topic_registry'").fetchone():
            raise SystemExit('Use a separate build database for v2 embeddings; do not mix inputs in a live registry.')
        embed_pending(conn, version=args.version, limit=args.limit)
    finally:
        conn.close()
