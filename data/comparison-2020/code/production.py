"""Small persistent layer for topic centers, weekly maintenance, and newsletters."""
import math
import json
import os
import secrets
import ssl
import smtplib
import sqlite3
import time
from email.message import EmailMessage

import numpy as np
from sklearn.cluster import MiniBatchKMeans

from core import SEED, unit
from topics import MODEL_PATH, name_topic
import routing

DAY = 86400
WINDOW = 28 * DAY
# Legacy model admission floor; ambiguous existing topics are not novelty.
THRESHOLD = routing.MIN_FIT
BIRTH_BAR = 0.45


def week_key(now):
    """UTC Monday-based week; timer jitter must not accidentally skip a week."""
    return (int(now)+3*DAY)//(7*DAY)

SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_candidates (
 id INTEGER PRIMARY KEY, centroid BLOB NOT NULL, first_seen INTEGER NOT NULL,
 last_seen INTEGER NOT NULL, support INTEGER NOT NULL, story_ids TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', topic INTEGER
);
CREATE TABLE IF NOT EXISTS topic_registry (
 id INTEGER PRIMARY KEY, centroid BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 parent INTEGER, born INTEGER NOT NULL, reviewed_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS maintenance (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions (
 token TEXT PRIMARY KEY, email TEXT NOT NULL, topic INTEGER NOT NULL,
 cadence TEXT NOT NULL CHECK(cadence IN ('weekly','monthly')),
 confirmed INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
 last_sent INTEGER NOT NULL DEFAULT 0,
 UNIQUE(email, topic, cadence)
);
CREATE INDEX IF NOT EXISTS subscription_topic ON subscriptions(topic);
CREATE TABLE IF NOT EXISTS signup_attempts (
 actor TEXT NOT NULL, at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS signup_attempts_actor ON signup_attempts(actor,at);
"""


def setup(conn, now=None):
    conn.executescript(SCHEMA + routing.SCHEMA)
    if conn.execute('SELECT 1 FROM topic_registry LIMIT 1').fetchone():
        return
    model = np.load(MODEL_PATH)
    now = int(time.time()) if now is None else now
    conn.executemany('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                     [(i, v.astype('<f4').tobytes(), now) for i, v in enumerate(model['centroids'])])
    conn.execute("INSERT OR IGNORE INTO maintenance VALUES ('weekly', ?)", (now,))
    conn.commit()


def model(conn):
    base = np.load(MODEL_PATH)
    mean = base['mean']
    rows = conn.execute("SELECT id, centroid FROM topic_registry WHERE status='active' ORDER BY id").fetchall()
    if not rows:
        raise RuntimeError('No active topics in the registry')
    return mean, np.array([r[0] for r in rows]), np.stack([np.frombuffer(r[1], '<f4') for r in rows])


def classify(conn, ids, vectors):
    mean, topic_ids, centers = model(conn)
    top, fit, margin = routing.decisions(vectors, mean, centers)
    return [(int(i), int(topic_ids[t]), float(f), float(m))
            for i, t, f, m in zip(ids, top, fit, margin)]


def input_version():
    with np.load(MODEL_PATH) as artifact:
        return int(artifact['input_version']) if 'input_version' in artifact else 1


def recent_vectors(conn, since, until=None):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(embeddings)')}
    version_filter = f' AND e.input_version={input_version()}' if 'input_version' in columns else ''
    rows = conn.execute('''SELECT s.id, coalesce(st.topic,-1), s.title, s.score, s.time, e.vec
        FROM stories s JOIN embeddings e USING(id) LEFT JOIN story_topics st USING(id)
        WHERE s.time >= ? AND s.dead=0 AND s.deleted=0''' + version_filter
        + (' AND s.time<=?' if until is not None else ''),
        (since,until) if until is not None else (since,)).fetchall()
    if not rows:
        return [], np.empty((0, 512), dtype='f4')
    mean = np.load(MODEL_PATH)['mean']
    return rows, unit(np.frombuffer(b''.join(r[5] for r in rows), '<f4').reshape(len(rows), -1) - mean)


def child_position(conn, parent):
    x, y = conn.execute('SELECT x,y FROM topics WHERE id=?', (parent,)).fetchone()
    next_id = conn.execute('SELECT coalesce(max(id),-1)+1 FROM topics').fetchone()[0]
    angle = next_id * 2.39996
    return (min(.98, max(.02, x + .035 * math.cos(angle))),
            min(.98, max(.02, y + .035 * math.sin(angle))))


def drift(conn, ids, centers, labels, times, x, now):
    scores = x @ centers.T
    top = scores.argmax(1)
    second = np.partition(scores,-2,axis=1)[:,-2] if len(ids)>1 else np.full(len(x),-1.)
    for j, topic in enumerate(ids):
        own = scores[:, j]
        mask = ((labels == topic) & (times >= now - 7 * DAY) & (top == j)
                & (own >= THRESHOLD) & (own - second >= routing.MIN_MARGIN))
        if mask.sum() >= 10:
            centers[j] = unit(.95 * centers[j] + .05 * unit(x[mask].mean(0)))
            conn.execute('UPDATE topic_registry SET centroid=? WHERE id=?',
                         (centers[j].astype('<f4').tobytes(), int(topic)))


def supported_candidate(vectors, times, sources, keys, now):
    """A repeated, coherent subject across independent articles and time."""
    if len(set(keys)) < 40 or len({s for s in sources if s}) < 3:
        return False
    if sum(t < now-7*DAY for t in times) < 10 or sum(t >= now-7*DAY for t in times) < 10:
        return False
    center = unit(vectors.mean(0))
    return float((vectors @ center).mean()) >= BIRTH_BAR


def births(conn, ids, centers, labels, rows, x, now):
    """Discover from poor-fit stories; promote only after two weekly observations.

    Ambiguous matches to existing topics are routed for review, not split into
    more overlapping topics. Existing subscriptions are never copied to children.
    """
    events = []
    similarity = (x @ centers.T).max(1)
    out = np.flatnonzero(similarity < THRESHOLD)
    if len(out) < 40:
        return events
    # A flood of resubmissions must not manufacture a new topic.
    unique = {}
    details = {}
    for i in out:
        row = conn.execute('SELECT title,url,text FROM stories WHERE id=?', (rows[i][0],)).fetchone()
        key, _, source = routing.metadata(*row)
        unique.setdefault(key, int(i)); details[int(i)] = (key, source)
    out = np.array(list(unique.values()))
    if len(out) < 40:
        return events
    km = MiniBatchKMeans(max(1, min(16, len(out)//80)), n_init=3, random_state=SEED).fit(x[out])
    known = list(centers)
    naming_calls = 0
    for label in range(km.n_clusters):
        members = out[km.labels_ == label]
        if not supported_candidate(x[members], [rows[i][4] for i in members],
                                   [details[i][1] for i in members], [details[i][0] for i in members], now):
            continue
        center = unit(x[members].mean(0))
        if max(float(c @ center) for c in known) >= .75:
            continue
        candidate = None
        for stored in conn.execute("SELECT id,centroid,first_seen,last_seen,status FROM topic_candidates WHERE status!='published'"):
            if float(np.frombuffer(stored[1], '<f4') @ center) >= .85:
                candidate = stored; break
        item_ids = json.dumps([int(rows[i][0]) for i in members])
        if candidate is None:
            conn.execute('INSERT INTO topic_candidates(centroid,first_seen,last_seen,support,story_ids) VALUES (?,?,?,?,?)',
                         (center.astype('<f4').tobytes(), now, now, len(members), item_ids))
            continue
        ident, _, first, last, status = candidate
        if status == 'rejected' and now-last < WINDOW:
            continue
        if now-last > 14*DAY or status == 'rejected':
            first = now
        conn.execute("UPDATE topic_candidates SET centroid=?,first_seen=?,last_seen=?,support=?,story_ids=?,status='pending' WHERE id=?",
                     (center.astype('<f4').tobytes(), first, now, len(members), item_ids, ident))
        if now-first < 7*DAY or not os.getenv('OPENAI_API_KEY'):
            continue
        if naming_calls >= 3:
            continue
        fit = x[members] @ center
        margins = fit - (x[members] @ np.stack(known).T).max(1)
        members = members[(fit >= THRESHOLD) & (margins >= routing.MIN_MARGIN)]
        if len(members) < 40:
            continue
        typical = sorted(members, key=lambda i: -float(x[i] @ center))[:10]
        varied = np.random.default_rng(SEED).choice(members, min(10, len(members)), replace=False)
        titles = [rows[i][2] for i in dict.fromkeys([*typical, *varied])]
        naming_calls += 1
        named = name_topic(titles, [], avoid=[f'{r[0]}: {r[1]}' for r in conn.execute(
            "SELECT t.name,t.description FROM topics t JOIN topic_registry r ON r.id=t.id WHERE r.status='active'")])
        if named.get('is_subject') is not True:
            conn.execute("UPDATE topic_candidates SET status='rejected' WHERE id=?", (ident,))
            continue
        if not isinstance(named.get('name'), str) or not isinstance(named.get('description'), str):
            raise ValueError('Invalid candidate topic name')
        xy = child_position(conn, int(ids[(centers @ center).argmax()]))
        child = conn.execute('INSERT INTO topics(name,description,size,cohesion,x,y) VALUES (?,?,?,?,?,?)',
                             (named['name'],named['description'],len(members),float((x[members]@center).mean()),*xy)).lastrowid
        conn.execute('INSERT INTO topic_registry(id,centroid,born) VALUES (?,?,?)',
                     (child,center.astype('<f4').tobytes(),now))
        for i in members:
            fit = float(x[i] @ center)
            margin = fit - max(float(x[i] @ c) for c in known)
            conn.execute('INSERT OR REPLACE INTO story_topics(id,topic,sim,margin) VALUES (?,?,?,?)',
                         (rows[i][0],child,fit,margin))
            conn.execute('DELETE FROM classification_queue WHERE id=?', (rows[i][0],))
        conn.execute("UPDATE topic_candidates SET status='published',topic=? WHERE id=?", (child,ident))
        known.append(center)
        events.append(('birth',child))
    return events


def weekly(conn, now=None):
    """Advance centers and registry from a bounded recent window; IDs never change."""
    now = int(time.time()) if now is None else now
    setup(conn, now=now)
    last = conn.execute("SELECT value FROM maintenance WHERE key='weekly'").fetchone()[0]
    if week_key(now) <= week_key(last):
        return []
    rows, x = recent_vectors(conn, now - WINDOW, until=now)
    if not rows:
        return []
    _, ids, centers = model(conn)
    labels = np.array([r[1] for r in rows])
    times = np.array([r[4] for r in rows])
    drift(conn, ids, centers, labels, times, x, now)
    events = births(conn, ids, centers, labels, rows, x, now)
    # Reload after births, so structural proposals see the current taxonomy.
    import structure
    _, ids, centers = model(conn)
    events.extend(structure.maintain(conn, ids, centers, rows, x, now))
    conn.execute('UPDATE topics SET size=(SELECT count(*) FROM story_topics st WHERE st.topic=topics.id), '
                 'cohesion=(SELECT coalesce(avg(sim),0) FROM story_topics st WHERE st.topic=topics.id)')
    conn.execute("UPDATE maintenance SET value=? WHERE key='weekly'", (now,))
    conn.commit()
    return events


def ranked_posts(conn, topic, cadence, now=None, limit=8):
    if cadence not in ('weekly', 'monthly'):
        raise ValueError('Invalid cadence')
    now = int(time.time()) if now is None else now
    days = 7 if cadence == 'weekly' else 30
    rows = conn.execute('''SELECT s.id,s.title,s.url,s.score,s.descendants,s.time,st.sim
        FROM story_topics st JOIN stories s USING(id) WHERE st.topic=? AND s.time>=? AND s.time<=?
        AND s.dead=0 AND s.deleted=0 AND s.score>=3 ORDER BY s.score DESC,s.id DESC LIMIT 300''',
        (topic, now-days*DAY, now)).fetchall()
    # Rank by score and discussion, with a modest freshness bonus. Penalize uncertain filing.
    def rank(r):
        age = max(0, (now-r[5])/DAY)
        return ((r[3] or 0) + 2*(r[4] or 0)) / (1 + age/days)**.7 * max(.4, min(1, (r[6] or 0)/.5))
    return [dict(id=r[0], title=r[1], url=r[2], score=r[3], comments=r[4], time=r[5])
            for r in sorted(rows, key=rank, reverse=True)[:limit]]


def resolve_topic(conn, topic):
    """Follow permanent aliases, including topics merged more than once."""
    seen = set()
    while topic not in seen:
        seen.add(topic)
        row = conn.execute('SELECT status,parent FROM topic_registry WHERE id=?', (topic,)).fetchone()
        if not row or row[0] == 'active':
            if conn.execute('SELECT 1 FROM topics WHERE id=?', (topic,)).fetchone():
                return topic
            break
        if row[0] != 'merged':
            break
        topic = row[1]
    raise ValueError('Topic not found')


def subscribe(conn, email, topic, cadence):
    topic = resolve_topic(conn, topic)
    if cadence not in ('weekly', 'monthly') or not conn.execute('SELECT 1 FROM topics WHERE id=?', (topic,)).fetchone():
        raise ValueError('Invalid topic or cadence')
    token = secrets.token_urlsafe(24)
    conn.execute('''INSERT INTO subscriptions VALUES (?,?,?,?,0,?,0)
        ON CONFLICT(email,topic,cadence) DO NOTHING''',
        (token, email.strip().lower(), topic, cadence, int(time.time())))
    conn.commit()
    return conn.execute('SELECT token FROM subscriptions WHERE email=? AND topic=? AND cadence=?',
                        (email.strip().lower(), topic, cadence)).fetchone()[0]


def allow_signup(conn, ip, email):
    now = int(time.time())
    conn.execute('DELETE FROM signup_attempts WHERE at<?', (now - DAY,))
    for actor, since, limit in ((f'ip:{ip}', now - 3600, 5),
                                (f'email:{email.lower()}', now - DAY, 3)):
        count = conn.execute('SELECT count(*) FROM signup_attempts WHERE actor=? AND at>=?',
                             (actor, since)).fetchone()[0]
        if count >= limit:
            conn.commit()
            return False
    conn.executemany('INSERT INTO signup_attempts VALUES (?,?)',
                     [(f'ip:{ip}', now), (f'email:{email.lower()}', now)])
    conn.commit()
    return True


def send(to, subject, body, unsubscribe_url=None):
    host = os.getenv('SMTP_HOST')
    sender = os.getenv('SMTP_FROM')
    if not host or not sender:
        raise RuntimeError('SMTP_HOST and SMTP_FROM are required for email delivery')
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = sender, to, subject
    if unsubscribe_url:
        msg['List-Unsubscribe'] = f'<{unsubscribe_url}>'
        msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        msg['Precedence'] = 'bulk'
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.getenv('SMTP_PORT', '587')), timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if os.getenv('SMTP_USER'):
            smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
        smtp.send_message(msg)


def deliver(conn, base_url):
    now = int(time.time())
    sent = 0
    failures = 0
    groups = {}
    for token, email, topic, cadence, last in conn.execute('''SELECT token,email,topic,cadence,last_sent
        FROM subscriptions WHERE confirmed=1''').fetchall():
        key = (email, resolve_topic(conn,topic), cadence)
        groups.setdefault(key,[]).append((token,last))
    for (email, topic, cadence), subscriptions in groups.items():
        token = subscriptions[0][0]
        last = max(row[1] for row in subscriptions)
        period = (7 if cadence == 'weekly' else 30)*DAY
        if now-last < period:
            continue
        name = conn.execute('SELECT name FROM topics WHERE id=?', (topic,)).fetchone()[0]
        posts = ranked_posts(conn, topic, cadence, now)
        if not posts:
            continue
        body = f'{name} · {cadence} HN digest\n\n' + '\n\n'.join(
            f"{p['title']}\n{p['url'] or 'https://news.ycombinator.com/item?id='+str(p['id'])}\nHN discussion: https://news.ycombinator.com/item?id={p['id']}"
            for p in posts)
        unsubscribe_url = f'{base_url}/api/newsletter/unsubscribe/{token}'
        body += f'\n\nManage subscription: {unsubscribe_url}\n'
        for extra, _ in subscriptions[1:]:
            body += f'Additional subscription: {base_url}/api/newsletter/unsubscribe/{extra}\n'
        try:
            send(email, f'HN Topics · {name}', body, unsubscribe_url)
        except (OSError, smtplib.SMTPException):
            failures += 1
            continue
        conn.executemany('UPDATE subscriptions SET last_sent=? WHERE token=?',
                         [(now,t) for t,_ in subscriptions])
        conn.commit()
        sent += 1
    if failures:
        raise RuntimeError(f'{failures} digest emails failed; {sent} succeeded')
    return sent


def audit_due(conn, now=None):
    now = int(time.time()) if now is None else now
    row = conn.execute("SELECT value FROM maintenance WHERE key='audit'").fetchone()
    return not row or now-row[0] >= 7*DAY


def mark_audited(conn, now=None):
    now = int(time.time()) if now is None else now
    conn.execute("INSERT OR REPLACE INTO maintenance VALUES ('audit',?)", (now,))
    conn.commit()
