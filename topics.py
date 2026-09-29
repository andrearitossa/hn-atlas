"""Curated topic model, stable registry, and vector iteration."""
import re
import math
import unicodedata
from collections import Counter
import time
import numpy as np
from config import MODEL_PATH
from core import unit
import routing

DAY = 86400

TOPIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY, name TEXT, description TEXT,
    size INTEGER, cohesion REAL, x REAL, y REAL
);
CREATE TABLE IF NOT EXISTS story_topics (
    id INTEGER PRIMARY KEY REFERENCES stories(id),
    topic INTEGER REFERENCES topics(id), sim REAL, margin REAL
);
CREATE INDEX IF NOT EXISTS idx_story_topics_topic ON story_topics(topic);
CREATE TABLE IF NOT EXISTS story_topic_labels (
    id INTEGER NOT NULL REFERENCES stories(id), topic INTEGER NOT NULL REFERENCES topics(id),
    rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 3),
    probability REAL NOT NULL CHECK(probability>0 AND probability<=1),
    model_version TEXT NOT NULL, assigned_at INTEGER NOT NULL,
    target_reached INTEGER NOT NULL CHECK(target_reached IN (0,1)),
    PRIMARY KEY(id,topic), UNIQUE(id,rank)
);
CREATE INDEX IF NOT EXISTS idx_story_topic_labels_topic ON story_topic_labels(topic,id);
CREATE TRIGGER IF NOT EXISTS story_topics_clear_labels_delete AFTER DELETE ON story_topics
BEGIN DELETE FROM story_topic_labels WHERE id=OLD.id; END;
CREATE TRIGGER IF NOT EXISTS story_topics_clear_labels_insert AFTER INSERT ON story_topics
BEGIN DELETE FROM story_topic_labels WHERE id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS story_topics_clear_labels_update AFTER UPDATE OF topic ON story_topics
BEGIN DELETE FROM story_topic_labels WHERE id=OLD.id; END;
CREATE VIEW IF NOT EXISTS story_topic_memberships AS
SELECT id,topic,sim,margin FROM story_topics
UNION ALL
SELECT l.id,l.topic,NULL,NULL FROM story_topic_labels l JOIN story_topics p ON p.id=l.id
WHERE l.rank>1 AND l.topic!=p.topic;

"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_registry (
 id INTEGER PRIMARY KEY, centroid BLOB NOT NULL, born INTEGER NOT NULL, reviewed_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS maintenance (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""

def setup(conn, now=None):
    conn.executescript(TOPIC_SCHEMA + SCHEMA + routing.SCHEMA)
    if conn.execute('SELECT 1 FROM topic_registry LIMIT 1').fetchone():
        return
    model = np.load(MODEL_PATH)
    now = int(time.time()) if now is None else now
    conn.executemany('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                     [(int(i), v.astype('<f4').tobytes(), now) for i, v in zip(model.get('topic_ids', np.arange(len(model['centroids']))), model['centroids'])])
    conn.commit()

def model(conn):
    base = np.load(MODEL_PATH)
    mean = base['mean']
    rows = conn.execute('SELECT id, centroid FROM topic_registry ORDER BY id').fetchall()
    if not rows:
        raise RuntimeError('No active topics in the registry')
    return mean, np.array([r[0] for r in rows]), np.stack([np.frombuffer(r[1], '<f4') for r in rows])


def child_position(conn, parent):
    """Place a new topic near its closest existing neighbor."""
    x, y = conn.execute('SELECT x,y FROM topics WHERE id=?', (parent,)).fetchone()
    next_id = conn.execute('SELECT coalesce(max(id),-1)+1 FROM topics').fetchone()[0]
    angle = next_id * 2.39996
    return (min(.98, max(.02, x + .035 * math.cos(angle))),
            min(.98, max(.02, y + .035 * math.sin(angle))))


def iter_vectors(conn, where="1", params=(), size=50_000):
    """Yield (ids, raw vectors) in chunks."""
    cur = conn.execute(f"SELECT e.id, e.vec FROM embeddings e JOIN stories s USING (id) WHERE {where}", params)
    while batch := cur.fetchmany(size):
        yield [r[0] for r in batch], np.frombuffer(b"".join(r[1] for r in batch), "<f4").reshape(len(batch), -1)


def slugs(conn):
    """Use readable ASCII names, reserving numeric paths for permanent IDs."""
    bases = {}
    for topic_id, name in conn.execute('SELECT id,name FROM topics ORDER BY id'):
        name = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
        base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        if not base or base.isdigit():
            base = f'topic-{topic_id}'
        bases[topic_id] = base
    counts = Counter(bases.values())
    result, used = {}, set()
    for topic_id, base in bases.items():
        slug = base if counts[base] == 1 else f'{base}-{topic_id}'
        while slug in used or (slug != base and slug in counts):
            slug += f'-{topic_id}'
        result[topic_id] = slug
        used.add(slug)
    return result


def memberships(conn):
    """All topic memberships, with read-only compatibility for older databases."""
    return 'story_topic_memberships' if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='story_topic_memberships'"
    ).fetchone() else 'story_topics'
