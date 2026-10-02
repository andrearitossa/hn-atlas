"""Incrementally mirror the recent Search corpus to Cloudflare D1 and Vectorize."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import numpy as np
import requests
from database import connect
from config import DB
import catalog
from embed import MODEL, DIM
from search_export import cutoff
import topics

ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / 'migrations/search/0001_search.sql'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def changes(post, old):
    """Numbers affect row hashes; vectors and indexed topic metadata have separate hashes."""
    row_hash = digest([post[k] for k in ('id', 'title', 'url', 'time', 'score', 'descendants')] + [post['topics']])
    vector_hash = digest(['topic-metadata-v1', post['vector_hash'], post['topics']])
    return row_hash, vector_hash, old is None or row_hash != old['row_hash'], old is None or vector_hash != old['vector_hash']


def vector_ids(post):
    if not post['vector_hash']:
        return []
    return [str(post['id'])]


def vectors(post):
    if not post['vector_hash']:
        return []
    values = [round(float(v), 7) for v in np.frombuffer(post['vec'], '<f4')]
    if len(post['topics']) > 3:
        raise ValueError('Search supports up to three topic memberships per post')
    metadata = {f'topic{i+1}': post['topics'][i] if i < len(post['topics']) else -1 for i in range(3)}
    return [dict(id=str(post['id']), values=values, metadata=metadata)]


def batch_sql(batch):
    """Bulk writes avoid one remote SQL execution per post or membership."""
    full, numbers, memberships = [], [], []
    for post, old, rh, vh, row_changed, vector_changed in batch:
        if not row_changed:
            continue
        same_text = old and old['text_hash'] == digest([post['title'], post['url'], post['time']])
        same_topics = old and old['topic_hash'] == digest(post['topics'])
        if same_text and same_topics:
            numbers.append(post)
        else:
            full.append(post)
        if not same_topics:
            memberships.append(post)
    sql = []
    if full:
        sql.append('INSERT INTO posts(id,title,url,time,score,descendants) VALUES ' + ','.join(
            '(' + ','.join(literal(p[k]) for k in ('id','title','url','time','score','descendants')) + ')' for p in full)
            + ' ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,time=excluded.time,score=excluded.score,descendants=excluded.descendants;')
    if numbers:
        sql.append('UPDATE posts SET score=CASE id ' + ' '.join(f"WHEN {p['id']} THEN {p['score']}" for p in numbers)
                   + ' END, descendants=CASE id ' + ' '.join(f"WHEN {p['id']} THEN {p['descendants']}" for p in numbers)
                   + ' END WHERE id IN (' + ','.join(str(p['id']) for p in numbers) + ');')
    if memberships:
        sql.append('DELETE FROM post_topics WHERE id IN (' + ','.join(str(p['id']) for p in memberships) + ');')
        pairs = [f"({p['id']},{t})" for p in memberships for t in p['topics']]
        if pairs:
            sql.append('INSERT INTO post_topics(id,topic) VALUES ' + ','.join(pairs) + ';')
    return '\n'.join(sql)


class Cloudflare:
    def __init__(self, config):
        self.config = config
        token = os.environ.get('CLOUDFLARE_API_TOKEN')
        if not token:
            result = subprocess.run([str(ROOT/'node_modules/.bin/wrangler'), 'auth', 'token', '--json'],
                                    cwd=ROOT, capture_output=True, text=True, timeout=60, check=True)
            token = json.loads(result.stdout)['token']
        self.session = requests.Session()
        self.session.headers['Authorization'] = f'Bearer {token}'
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{config['account_id']}"
        self.written = 0
        self.last_mutation = None

    def api(self, method, path, **kwargs):
        for attempt in range(4):
            try:
                response = self.session.request(method, self.base+path, timeout=60, **kwargs)
                data = response.json()
                if response.ok and data.get('success'):
                    return data['result']
                if response.status_code != 429 and response.status_code < 500:
                    raise RuntimeError(f"Cloudflare {path} failed: {data.get('errors')}")
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 3:
                    raise
            if attempt == 3:
                raise RuntimeError(f'Cloudflare request failed: {path}')
            time.sleep(2 ** attempt)

    def sql(self, sql):
        result = self.api('POST', f"/d1/database/{self.config['database_id']}/query", json={'sql': sql})
        if not all(item.get('success') for item in result):
            raise RuntimeError('D1 statements failed')
        self.written += sum(item.get('meta', {}).get('rows_written', 0) for item in result)
        return result

    def upsert(self, rows):
        # Keep batches under API payload limits, with several vectors per request.
        for offset in range(0, len(rows), 2000):
            payload = '\n'.join(json.dumps(v, separators=(',', ':')) for v in rows[offset:offset+2000]) + '\n'
            result = self.api('POST', f"/vectorize/v2/indexes/{self.config['index_name']}/upsert",
                     files={'vectors': ('vectors.ndjson', payload, 'application/x-ndjson')})
            self.last_mutation = result.get('mutationId')

    def delete_vectors(self, ids):
        for offset in range(0, len(ids), 100):
            result = self.api('POST', f"/vectorize/v2/indexes/{self.config['index_name']}/delete_by_ids", json={'ids': ids[offset:offset+100]})
            self.last_mutation = result.get('mutationId')


def load_posts(conn, now):
    since = cutoff(now)
    membership = {}
    for row in conn.execute(f'''SELECT st.id,st.topic FROM {topics.memberships(conn)} st JOIN stories s USING(id)
        WHERE s.time>=? AND s.time<=? AND s.dead=0 AND s.deleted=0 AND s.title IS NOT NULL''', (since, now)):
        membership.setdefault(row['id'], set()).add(row['topic'])
    for row in conn.execute('''SELECT s.id,s.title,s.url,s.time,s.score,s.descendants,e.vec,e.model
        FROM stories s LEFT JOIN embeddings e USING(id)
        WHERE s.time>=? AND s.time<=? AND s.dead=0 AND s.deleted=0 AND s.title IS NOT NULL ORDER BY s.id''', (since, now)):
        post = dict(row)
        post['score'] = post['score'] or 0
        post['descendants'] = post['descendants'] or 0
        post['topics'] = sorted(membership.get(post['id'], ()))
        vec = post['vec']
        valid = post['model'] == MODEL and vec and len(vec) == DIM * 4
        if valid:
            value = np.frombuffer(vec, '<f4')
            valid = np.isfinite(value).all() and np.linalg.norm(value) > 0
        post['vector_hash'] = hashlib.sha256(vec).hexdigest() if valid else ''
        yield post


def sync(conn, ledger, cloud, now):
    ledger.row_factory = sqlite3.Row
    ledger.executescript('''CREATE TABLE IF NOT EXISTS synced (
        id INTEGER PRIMARY KEY, row_hash TEXT NOT NULL, vector_hash TEXT NOT NULL, vector_ids TEXT NOT NULL, text_hash TEXT NOT NULL, topic_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
    # A ledger belongs to exactly one pair of remote stores.
    identity = digest(cloud.config)
    stored = ledger.execute("SELECT value FROM settings WHERE key='remote'").fetchone()
    if stored and stored['value'] != identity:
        raise RuntimeError('Search sync ledger belongs to different Cloudflare resources; use a new ledger')
    ledger.execute("INSERT OR IGNORE INTO settings VALUES('remote',?)", (identity,));ledger.commit()
    previous = {row['id']: dict(row) for row in ledger.execute('SELECT * FROM synced')}
    seen = set()
    counts = dict(posts=0, rows_changed=0, vectors_upserted=0, vectors_deleted=0, posts_deleted=0)
    batch = []
    batch_bytes = 0

    def flush():
        nonlocal batch_bytes
        if not batch:
            return
        sql, upserts, deletes = batch_sql(batch), [], []
        for post, old, rh, vh, row_changed, vector_changed in batch:
            if row_changed:
                counts['rows_changed'] += 1
            if vector_changed:
                upserts.extend(vectors(post))
                deletes.extend(set(json.loads(old['vector_ids']) if old else []) - set(vector_ids(post)))
        # Separate stores can acknowledge independently; only the combined success checkpoints.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = []
            if sql:
                futures.append(pool.submit(cloud.sql, sql))
            if upserts:
                futures.append(pool.submit(cloud.upsert, upserts))
            for future in futures:
                future.result()
        if deletes:
            cloud.delete_vectors(deletes)
        counts['vectors_upserted'] += len(upserts);counts['vectors_deleted'] += len(deletes)
        # Advance only after both services acknowledged. Repeating a partial batch is safe.
        ledger.executemany('INSERT OR REPLACE INTO synced VALUES(?,?,?,?,?,?)',
                           [(p['id'], rh, vh, json.dumps(vector_ids(p)), digest([p['title'], p['url'], p['time']]), digest(p['topics'])) for p, old, rh, vh, rc, vc in batch])
        ledger.commit();batch.clear();batch_bytes = 0
        print(json.dumps(counts), flush=True)

    for post in load_posts(conn, now):
        counts['posts'] += 1;seen.add(post['id'])
        old = previous.get(post['id'])
        rh, vh, rc, vc = changes(post, old)
        if rc or vc:
            entry = (post, old, rh, vh, rc, vc)
            entry_bytes = (sum(len(literal(post[k]).encode()) for k in ('id','title','url','time','score','descendants')) + len(post['topics'])*30 + 120) if rc else 0
            if batch and batch_bytes + entry_bytes > 85000:
                flush()
            batch.append(entry);batch_bytes += entry_bytes
            if len(batch) >= (500 if batch_bytes else 2000):
                flush()
    flush()
    expired = sorted(previous.keys()-seen)
    for start in range(0, len(expired), 100):
        ids = expired[start:start+100]
        cloud.sql('DELETE FROM posts WHERE id IN (' + ','.join(map(str, ids)) + ');')
        vector_deletes = [v for ident in ids for v in json.loads(previous[ident]['vector_ids'])]
        if vector_deletes:
            cloud.delete_vectors(vector_deletes)
        ledger.executemany('DELETE FROM synced WHERE id=?', [(i,) for i in ids]);ledger.commit()
        counts['posts_deleted'] += len(ids);counts['vectors_deleted'] += len(vector_deletes)
    names = conn.execute('SELECT id,name FROM topics ORDER BY id').fetchall()
    topic_hash = digest([list(r) for r in names])
    old_topics = ledger.execute("SELECT value FROM settings WHERE key='topics'").fetchone()
    if not old_topics or old_topics['value'] != topic_hash:
        # Topic registry is small; replacing it does not touch posts or their indexes.
        cloud.sql('DELETE FROM topics;' + ('INSERT INTO topics(id,name) VALUES ' + ','.join(f"({r[0]},{literal(r[1])})" for r in names) + ';' if names else ''))
        ledger.execute("INSERT OR REPLACE INTO settings VALUES('topics',?)", (topic_hash,));ledger.commit()
    cloud.sql(f"INSERT INTO search_state VALUES('as_of',{now}),('count',{counts['posts']}) ON CONFLICT(key) DO UPDATE SET value=excluded.value;")
    ledger.execute("INSERT OR REPLACE INTO settings VALUES('as_of',?)", (str(now),))
    ledger.execute("INSERT OR REPLACE INTO settings VALUES('layout','topic-metadata-v1')");ledger.commit()
    counts['d1_rows_written'] = cloud.written
    mutation = getattr(cloud, 'last_mutation', None)
    if mutation:
        ledger.execute("INSERT OR REPLACE INTO settings VALUES('mutation',?)", (mutation,));ledger.commit()
        counts['vector_mutation'] = mutation
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(DB))
    parser.add_argument('--config', default=str(ROOT/'search-cloudflare.json'))
    parser.add_argument('--ledger', default=str(ROOT/'data/search-sync.sqlite'))
    parser.add_argument('--as-of', type=int)
    parser.add_argument('--init-schema', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    with open(str(args.ledger)+'.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cloud = Cloudflare(config)
        if args.init_schema:
            cloud.sql(SCHEMA.read_text())
        with connect(args.db, readonly=True) as conn, sqlite3.connect(args.ledger) as ledger:
            conn.execute('BEGIN')  # One consistent local snapshot for rows, topics, and embeddings.
            now = args.as_of or catalog.now(conn)
            print(json.dumps(sync(conn, ledger, cloud, now)), flush=True)


if __name__ == '__main__':
    main()
