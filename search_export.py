"""A compact, public search snapshot: recent stories and compatible vectors only."""
import calendar
from datetime import datetime, timezone
import json
from pathlib import Path
import numpy as np
from embed import MODEL, DIM


def cutoff(as_of):
    date = datetime.fromtimestamp(as_of, timezone.utc)
    month_index = date.year * 12 + date.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    return int(date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1])).timestamp())


def write_summary(conn, output, as_of):
    """Small local preview manifest; production reads this information from D1."""
    target = Path(output) / 'search-data'
    target.mkdir(parents=True, exist_ok=True)
    since = cutoff(as_of)
    count = conn.execute("SELECT COUNT(*) FROM stories WHERE time>=? AND time<=? AND dead=0 AND deleted=0 AND title IS NOT NULL", (since, as_of)).fetchone()[0]
    topic_list = [dict(id=row[0], name=row[1]) for row in conn.execute('SELECT id,name FROM topics ORDER BY name')]
    (target/'index.json').write_text(json.dumps(dict(topics=topic_list,count=count,as_of=as_of,since=since)))


def write(conn, output, as_of):
    target = Path(output) / 'search-data'
    target.mkdir(parents=True, exist_ok=True)
    since = cutoff(as_of)
    has_vectors = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embeddings'").fetchone()
    fields = 'e.vec,e.model' if has_vectors else 'NULL AS vec,NULL AS model'
    join = 'LEFT JOIN embeddings e USING(id)' if has_vectors else ''
    rows = conn.execute(f"""SELECT s.id,s.title,s.url,s.time,s.score,s.descendants,{fields}
        FROM stories s {join}
        WHERE s.time>=? AND s.time<=? AND s.dead=0 AND s.deleted=0 AND s.title IS NOT NULL
        ORDER BY s.time DESC,s.id DESC""", (since, as_of))
    posts, vectors, vector_rows = [], [], []
    for row in rows:
        post = {key: row[key] for key in ('id','title','url','time','score','descendants')}
        post['vector'] = -1
        if row['model'] == MODEL and row['vec'] and len(row['vec']) == DIM * 4:
            vector = np.frombuffer(row['vec'], dtype='<f4')
            norm = np.linalg.norm(vector)
            if np.isfinite(vector).all() and norm > 0:
                post['vector'] = len(vector_rows)
                vector_rows.append(len(posts))
                vectors.append(np.rint(vector / norm * 127).astype(np.int8))
        posts.append(post)
    shards = []
    # Each shard stays comfortably under the Pages asset limit.
    for offset in range(0, len(vectors), 8192):
        name = f'vectors-{offset//8192}.bin'
        (target / name).write_bytes(np.stack(vectors[offset:offset+8192]).tobytes())
        shards.append({'file':name,'offset':offset,'count':min(8192,len(vectors)-offset)})
    topic_list = add_topics(conn, posts)
    data = dict(topics=topic_list, as_of=as_of, since=since, model=MODEL, dimensions=DIM, posts=posts, shards=shards)
    write_manifest(target, data)
    return data


def write_page(output, source, asset_root="/"):
    output, source = Path(output), Path(source)
    # Search lives on the homepage; remove any previous standalone export.
    import shutil
    if (output / 'search').exists():
        shutil.rmtree(output / 'search')
    for name in ('search.css','search.js','search-worker.js'):
        (output/name).write_text((source/name).read_text())


def from_snapshot(release, output, as_of):
    """UI-only rebuilds reuse vectors; old exports gain text search without API calls."""
    import shutil
    release, output = Path(release), Path(output)
    existing = release/'public'/'search-data'
    if existing.exists():
        shutil.copytree(existing, output/'search-data')
        return
    posts = {}
    since = cutoff(as_of)
    for file in (release/'stories').glob('*.json'):
        for post in json.loads(file.read_text()):
            if since <= post['time'] <= as_of and not post.get('dead') and not post.get('deleted'):
                previous_topics = posts.get(post['id'], {}).get('topics', [])
                posts[post['id']] = {'topics':previous_topics + [int(file.stem)], **{key:post.get(key) for key in ('id','title','url','time','score','descendants')},'vector':-1}
    target = output/'search-data'
    target.mkdir(parents=True)
    overview=json.loads((release/'topics.json').read_text())
    write_manifest(target, dict(topics=[{'id':t['id'],'name':t['name']} for t in overview['topics']],as_of=as_of,since=since,model=MODEL,dimensions=DIM,shards=[],posts=sorted(posts.values(),key=lambda p:-p['time'])))



def write_manifest(target, data):
    manifest = {key:value for key,value in data.items() if key != 'posts'}
    manifest['post_shards'] = []
    for offset in range(0, len(data['posts']), 4096):
        name = f'posts-{offset//4096}.json'
        (target/name).write_text(json.dumps(data['posts'][offset:offset+4096],ensure_ascii=False,separators=(',',':')))
        manifest['post_shards'].append(name)
    manifest['count'] = len(data['posts'])
    (target/'index.json').write_text(json.dumps(manifest,separators=(',',':')))



def add_topics(conn, posts):
    import topics as topic_registry
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'topics' not in tables or 'story_topics' not in tables:
        return []
    by_id = {post['id']:post for post in posts}
    for post in posts:
        post['topics'] = []
    if posts:
        since, until = min(p['time'] for p in posts), max(p['time'] for p in posts)
        for ident, topic in conn.execute(f'SELECT st.id,st.topic FROM {topic_registry.memberships(conn)} st JOIN stories s USING(id) WHERE s.time>=? AND s.time<=?',(since,until)):
            if ident in by_id:
                by_id[ident]['topics'].append(topic)
    return [dict(id=row[0],name=row[1]) for row in conn.execute('SELECT id,name FROM topics ORDER BY name')]
