"""Exact, bounded-memory cosine search over every compatible corpus vector."""
from functools import lru_cache
import numpy as np
from embed import DIM, MODEL, embed_batch


@lru_cache(maxsize=128)
def query_vector(query):
    return unit(np.frombuffer(embed_batch([query])[0], dtype='<f4'))


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if vector.shape != (DIM,) or not np.isfinite(vector).all() or norm == 0:
        raise ValueError('Invalid embedding vector')
    return vector / norm


def search(conn, vector, *, topic_id=None, topic_weight=0.15, limit=30):
    query = unit(vector)
    topic = None
    if topic_id is not None:
        row = conn.execute('SELECT centroid FROM topic_registry WHERE id=?', (topic_id,)).fetchone()
        if row is None:
            raise ValueError('Topic has no semantic vector')
        topic = unit(np.frombuffer(row[0], dtype='<f4'))
    # No topic membership or date restriction: every live compatible vector competes.
    cursor = conn.execute('''SELECT e.id,e.vec FROM embeddings e JOIN stories s USING(id)
        WHERE e.model=? AND length(e.vec)=? AND s.dead=0 AND s.deleted=0
        AND s.title IS NOT NULL''', (MODEL, DIM * 4))
    winners = []
    searched = 0
    while rows := cursor.fetchmany(4096):
        matrix = np.stack([np.frombuffer(row[1], dtype='<f4') for row in rows])
        norms = np.linalg.norm(matrix, axis=1)
        valid = np.isfinite(matrix).all(axis=1) & (norms > 0)
        matrix /= np.where(valid, norms, 1)[:, None]
        relevance = np.einsum('ij,j->i', matrix, query)
        affinity = np.einsum('ij,j->i', matrix, topic) if topic is not None else np.zeros(len(rows))
        scores = relevance + topic_weight * affinity
        indices = np.flatnonzero(valid)
        indices = sorted(indices, key=lambda i: (-float(scores[i]), rows[i][0]))[:limit]
        winners.extend((float(scores[i]), rows[i][0], float(relevance[i]), float(affinity[i])) for i in indices)
        winners.sort(key=lambda r: (-r[0], r[1]))
        winners = winners[:limit]
        searched += int(valid.sum())
    posts = []
    for score, ident, relevance, affinity in winners:
        post = dict(conn.execute('SELECT id,title,url,by,time,score,descendants FROM stories WHERE id=?', (ident,)).fetchone())
        post.update(search_score=score, query_similarity=relevance, topic_similarity=affinity)
        posts.append(post)
    return {'posts': posts, 'searched_vectors': searched, 'topic_weight': topic_weight if topic is not None else 0}
