"""Public read models shared by the API and static publisher."""
import time

import numpy as np

import topics
import attention

POST = "s.id, s.title, s.url, s.time, s.score, s.descendants"
DAY = 86400


def now(c) -> int:
    """'Now' = newest article in the db, so the numbers stay meaningful on a stale snapshot."""
    return c.execute("SELECT max(time) FROM stories WHERE dead=0 AND deleted=0 AND time<=?",
                     (int(time.time()),)).fetchone()[0] or int(time.time())

def overview(c):
    _, ids, C = topics.model(c)
    S = C @ C.T
    np.fill_diagonal(S, -1)
    t = now(c)
    count = lambda a, b: dict(c.execute(
        f"SELECT topic, count(*) FROM {topics.memberships(c)} JOIN stories s USING (id) WHERE s.dead=0 AND s.deleted=0 AND s.time >= ? AND s.time < ? "
        "GROUP BY topic", (a, b)).fetchall())
    recent = count(t - 30 * DAY, t + 1)
    week = count(t - 7 * DAY, t + 1)
    sizes = dict(c.execute(f'SELECT topic,count(*) FROM {topics.memberships(c)} JOIN stories s USING(id) '
                           'WHERE s.dead=0 AND s.deleted=0 GROUP BY topic'))
    rows = [dict(r) for r in c.execute("SELECT t.id, t.name, t.description, t.size, t.x, t.y FROM topics t "
                                       "JOIN topic_registry r ON r.id=t.id ORDER BY t.size DESC")]
    slugs = topics.slugs(c)
    for r in rows:
        r['slug'] = slugs[r['id']]
        r['size'] = sizes.get(r['id'], 0)
        r["last_30d"] = recent.get(r["id"], 0)
        r['last_7d'] = week.get(r['id'], 0)
    rows.sort(key=lambda r: r['size'], reverse=True)
    edges = {tuple(sorted((int(ids[i]), int(ids[j])))) for i in range(len(C)) for j in [j for j in np.argsort(-S[i]) if j != i][:2]}
    return {"as_of": t, "topics": rows, "edges": [dict(a=a, b=b) for a, b in sorted(edges)]}


def detail(c, topic_id, as_of=None):
    info = c.execute("SELECT id, name, description, size FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if not info:
        raise ValueError("Topic not found")
    t = now(c) if as_of is None else as_of
    q = lambda sql, *p: [dict(r) for r in c.execute(sql, (topic_id, *p))]
    base = f"SELECT {POST} FROM {topics.memberships(c)} st JOIN stories s USING (id) WHERE st.topic = ? AND s.dead = 0 AND s.deleted = 0"
    return {
        **dict(info),
        "slug": topics.slugs(c)[topic_id],
        "as_of": t,
        "size": c.execute(f'SELECT count(*) FROM {topics.memberships(c)} JOIN stories s USING(id) WHERE topic=? AND s.dead=0 AND s.deleted=0', (topic_id,)).fetchone()[0],
        "trending": attention.trending(c, topic_id, t),
        "top": q(f"{base} ORDER BY s.score DESC,s.id DESC LIMIT 10"),
        "monthly": q("SELECT strftime('%Y-%m', s.time, 'unixepoch') AS month, count(*) AS posts "
                     f"FROM {topics.memberships(c)} st JOIN stories s USING (id) WHERE st.topic = ? AND s.dead=0 AND s.deleted=0 "
                     "GROUP BY month ORDER BY month"),
    }
