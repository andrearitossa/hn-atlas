"""Public, read-mostly API. Scheduled work runs in jobs.py."""
import os
import sqlite3
import smtplib
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Literal
from html import escape

import catalog
import timeline
from catalog import DAY, POST, now
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

import production
from database import connect
from pydantic import BaseModel, Field

DB = os.environ.get("HN_DB", "data/hackernews.db")


@asynccontextmanager
async def lifespan(_app):
    with connect(DB) as conn:
        production.setup(conn)
    yield


app = FastAPI(title="HN Atlas", docs_url=None, redoc_url=None, lifespan=lifespan)


def db():
    return connect(DB, readonly=True)


def writable_db():
    return connect(DB)


def resolve_topic(c, topic_id):
    try:
        return production.resolve_topic(c, topic_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/health')
def health():
    try:
        with db() as c:
            for table in ('topics', 'story_topics', 'topic_registry', 'subscriptions'):
                c.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
            latest = c.execute('SELECT max(time) FROM stories WHERE dead=0 AND deleted=0').fetchone()[0]
    except sqlite3.Error as exc:
        raise HTTPException(503, 'Database is unavailable or not initialized') from exc
    return {'status': 'ok', 'latest_story_at': latest,
            'stale': latest is None or time.time() - latest > 10 * DAY}


# ---------- topics ----------



@app.get("/")
def index():
    return FileResponse("index.html")


@app.get("/data.js")
def data_script():
    return FileResponse("data.js", media_type="text/javascript")


@app.get("/api/topics")
def topics():
    return topic_list(int(time.time() // 300))


@lru_cache(maxsize=2)
def topic_list(_bucket):
    with db() as c:
        return catalog.overview(c)


@app.get("/api/topics/{topic_id}")
def topic(topic_id: int):
    with db() as c:
        result = catalog.detail(c, resolve_topic(c, topic_id))
    result['newsletter_available'] = all(os.getenv(k) for k in ('SMTP_HOST', 'SMTP_FROM', 'PUBLIC_URL'))
    return result


@app.get('/api/timeline')
def timeline_overview(days: int = 90):
    if days not in (30, 90, 365):
        raise HTTPException(422, 'days must be 30, 90, or 365')
    with db() as c:
        return timeline.build(c, days=days)


@app.get('/api/topics/{topic_id}/timeline')
def topic_timeline(topic_id: int, days: int = 0, view: Literal['history', 'zoom'] = 'history'):
    if days not in (0, 30, 90, 365):
        raise HTTPException(422, 'days must be 0, 30, 90, or 365')
    with db() as c:
        topic_id = resolve_topic(c, topic_id)
        if view == 'zoom':
            return timeline.zoom(c, topic_id)
        return timeline.history(c, topic_id) if days == 0 else timeline.build(c, topic_id, days)


class SubscriptionRequest(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    topic: int
    cadence: Literal['weekly', 'monthly'] = 'weekly'


@app.get('/api/topics/{topic_id}/stories')
def stories(
    topic_id: int,
    q: Annotated[str, Query(max_length=200)] = '',
    sort: Literal['newest', 'top', 'discussed'] = 'newest',
    days: Annotated[int, Query(ge=0, le=36500)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    year: Annotated[int, Query(ge=0, le=9998)] = 0,
):
    """Browse a topic's live stories; days=0 includes its entire history."""
    with db() as c:
        topic_id = resolve_topic(c, topic_id)
        as_of = now(c)
        where = 'st.topic=? AND s.dead=0 AND s.deleted=0 AND s.time<=?'
        params = [topic_id, as_of]
        if days:
            where += ' AND s.time>=?'
            params.append(as_of - days * DAY)
        if year:
            where += ' AND s.time>=? AND s.time<?'
            params.extend([int(datetime(year,1,1,tzinfo=timezone.utc).timestamp()),
                           int(datetime(year+1,1,1,tzinfo=timezone.utc).timestamp())])
        if q.strip():
            where += " AND (s.title LIKE ? ESCAPE '\\' OR s.url LIKE ? ESCAPE '\\')"
            term = q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            params.extend([f'%{term}%'] * 2)
        order = {'newest': 's.time DESC', 'top': 's.score DESC',
                 'discussed': 's.descendants DESC'}[sort]
        rows = [dict(r) for r in c.execute(
            f'SELECT {POST} FROM story_topics st JOIN stories s USING(id) '
            f'WHERE {where} ORDER BY {order},s.id DESC LIMIT ? OFFSET ?',
            (*params, limit + 1, offset))]
        return {'id': topic_id, 'as_of': as_of, 'posts': rows[:limit],
                'next_offset': offset + limit if len(rows) > limit else None}


@app.get('/api/topics/{topic_id}/digest')
def digest(topic_id: int, cadence: str = 'weekly'):
    if cadence not in ('weekly', 'monthly'):
        raise HTTPException(400, 'Invalid cadence')
    with db() as c:
        topic_id = resolve_topic(c, topic_id)
        row = c.execute('SELECT name FROM topics WHERE id=?', (topic_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Topic not found')
        as_of = now(c)
        return {'id': topic_id, 'name': row[0], 'cadence': cadence, 'as_of': as_of,
                'posts': production.ranked_posts(c, topic_id, cadence, now=as_of)}


@app.post('/api/newsletter/subscribe')
def subscribe(request: SubscriptionRequest, http_request: Request):
    if not all(os.getenv(k) for k in ('SMTP_HOST', 'SMTP_FROM', 'PUBLIC_URL')):
        raise HTTPException(503, 'Email delivery is not configured')
    with writable_db() as c:
        if not production.allow_signup(c, http_request.client.host if http_request.client else 'unknown', request.email):
            raise HTTPException(429, 'Too many signup attempts')
        try:
            token = production.subscribe(c, str(request.email), request.topic, request.cadence)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        link = os.environ['PUBLIC_URL'].rstrip('/') + '/api/newsletter/confirm/' + token
    try:
        production.send(str(request.email), 'Confirm your HN Atlas subscription',
                        f'Confirm your subscription: {link}\n\nIf this was not you, ignore this email.')
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(503, 'Could not send confirmation. Please try again later.') from exc
    return {'status': 'confirmation_sent'}


@app.get('/api/newsletter/confirm/{token}')
def confirm(token: str):
    return action_page('Confirm subscription', token, 'confirm')


def action_page(label, token, action):
    path = f'/api/newsletter/{action}/{escape(token, quote=True)}'
    return HTMLResponse(f'''<!doctype html><html lang="en"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{label} · HN Atlas</title>
        <body style="font:16px system-ui;max-width:36rem;margin:15vh auto;padding:1rem">
        <h1>{label}</h1><form method="post" action="{path}">
        <button style="padding:.7rem 1rem;font:inherit;cursor:pointer">{label}</button></form>
        <p><a href="/">Back to HN Atlas</a></p></body></html>''')


@app.post('/api/newsletter/confirm/{token}')
def confirm_post(token: str):
    with writable_db() as c:
        cur = c.execute('UPDATE subscriptions SET confirmed=1 WHERE token=?', (token,))
        c.commit()
        if not cur.rowcount:
            raise HTTPException(404, 'Subscription not found')
    return HTMLResponse('<p>Subscription confirmed. <a href="/">Back to HN Atlas</a></p>')


@app.get('/api/newsletter/unsubscribe/{token}')
def unsubscribe(token: str):
    return action_page('Unsubscribe', token, 'unsubscribe')


@app.post('/api/newsletter/unsubscribe/{token}')
def unsubscribe_post(token: str):
    with writable_db() as c:
        cur = c.execute('DELETE FROM subscriptions WHERE token=?', (token,))
        c.commit()
        if not cur.rowcount:
            raise HTTPException(404, 'Subscription not found')
    return HTMLResponse('<p>Unsubscribed. <a href="/">Back to HN Atlas</a></p>')
