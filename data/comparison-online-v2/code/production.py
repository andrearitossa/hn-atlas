"""Small persistent layer for topic centers, weekly maintenance, and newsletters."""
import math
import os
import secrets
import ssl
import smtplib
import time
from email.message import EmailMessage

import numpy as np

from core import unit
from topics import MODEL_PATH
import routing

DAY = 86400
WINDOW = 28 * DAY


def week_key(now):
    """UTC Monday-based week; timer jitter must not accidentally skip a week."""
    return (int(now)+3*DAY)//(7*DAY)

SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_registry (
 id INTEGER PRIMARY KEY, centroid BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 parent INTEGER, born INTEGER NOT NULL, reviewed_at INTEGER DEFAULT 0, min_fit REAL NOT NULL DEFAULT 0.2862
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
    if 'min_fit' not in {r[1] for r in conn.execute('PRAGMA table_info(topic_registry)')}:
        conn.execute('ALTER TABLE topic_registry ADD COLUMN min_fit REAL NOT NULL DEFAULT 0.2862')
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


def weekly(conn, now=None):
    """Advance centers and registry from a bounded recent window; IDs never change."""
    now = int(time.time()) if now is None else now
    setup(conn, now=now)
    checkpoint = conn.execute("SELECT value FROM maintenance WHERE key='weekly'").fetchone()
    last = checkpoint[0] if checkpoint else 0
    if week_key(now) <= week_key(last):
        return []
    rows, x = recent_vectors(conn, now - WINDOW, until=now)
    if not rows:
        return []
    import structure
    with conn:
        events = structure.maintain(conn, rows, x, now)
        conn.execute('UPDATE topics SET size=(SELECT count(*) FROM story_topics st WHERE st.topic=topics.id), '
                     'cohesion=(SELECT coalesce(avg(sim),0) FROM story_topics st WHERE st.topic=topics.id)')
        conn.execute("INSERT OR REPLACE INTO maintenance(key,value) VALUES ('weekly',?)", (now,))
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
            send(email, f'HN Atlas · {name}', body, unsubscribe_url)
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
