"""Public, read-mostly API. Scheduled work runs in refresh.py."""
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Literal

import catalog
import timeline
from catalog import DAY, POST, now
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pathlib import Path
import seo

import topics as topic_registry
from database import connect

from config import DB


@asynccontextmanager
async def lifespan(_app):
    with connect(DB) as conn:
        topic_registry.setup(conn)
    yield


app = FastAPI(title="HN Atlas", docs_url=None, redoc_url=None, lifespan=lifespan)


def db():
    return connect(DB, readonly=True)


def require_topic(c, topic_id):
    if c.execute('SELECT 1 FROM topics WHERE id=?', (topic_id,)).fetchone():
        return topic_id
    raise HTTPException(404, 'Topic not found')


@app.get('/health')
def health():
    try:
        with db() as c:
            for table in ('topics', 'story_topics', 'topic_registry'):
                c.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
            latest = c.execute('SELECT max(time) FROM stories WHERE dead=0 AND deleted=0').fetchone()[0]
            refreshed = c.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(503, 'Database is unavailable or not initialized') from exc
    return {'status': 'ok', 'latest_story_at': latest,
            'last_refresh_at': refreshed[0] if refreshed else None,
            'stale': not refreshed or time.time() - refreshed[0] > 2 * DAY}


# ---------- topics ----------


@app.get("/")
def index():
    with db() as c:
        return HTMLResponse(seo.render(Path("index.html").read_text(), overview=catalog.overview(c)))


@app.get('/topic/{topic_key}/')
def topic_page(topic_key: str):
    with db() as c:
        key = str(topic_key)
        slug_ids = {slug: ident for ident, slug in topic_registry.slugs(c).items()}
        topic_id = int(key) if key.isascii() and key.isdigit() else slug_ids.get(key)
        if topic_id is None:
            raise HTTPException(404, 'Topic not found')
        detail = catalog.detail(c, require_topic(c, topic_id))
        if key != detail['slug']:
            return RedirectResponse(seo.topic_path(detail), status_code=301)
        html = seo.render(Path('index.html').read_text(), topic=detail)
        return HTMLResponse(html.replace('src="./data.js"', 'src="/data.js"'))


@app.get('/sitemap.xml')
def sitemap():
    with db() as c:
        return Response(seo.sitemap(catalog.overview(c)), media_type='application/xml')


@app.get('/robots.txt')
def robots():
    return Response(f'User-agent: *\nAllow: /\nSitemap: {seo.ORIGIN}/sitemap.xml\n', media_type='text/plain')


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
        return catalog.detail(c, require_topic(c, topic_id))


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
        topic_id = require_topic(c, topic_id)
        if view == 'zoom':
            return timeline.zoom(c, topic_id)
        return timeline.history(c, topic_id) if days == 0 else timeline.build(c, topic_id, days)


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
        topic_id = require_topic(c, topic_id)
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
            f'SELECT {POST} FROM {catalog.topics.memberships(c)} st JOIN stories s USING(id) '
            f'WHERE {where} ORDER BY {order},s.id DESC LIMIT ? OFFSET ?',
            (*params, limit + 1, offset))]
        return {'id': topic_id, 'as_of': as_of, 'posts': rows[:limit],
                'next_offset': offset + limit if len(rows) > limit else None}


@app.get('/search')
@app.get('/search/')
@app.get('/search/index.html')
def search_page(request: Request):
    return RedirectResponse('/' + ('?' + request.url.query if request.url.query else ''), status_code=301)


@app.get('/search-data/{asset}')
def search_asset(asset: str):
    if asset != 'index.json' and not __import__('re').fullmatch(r'vectors-\d+\.bin', asset):
        raise HTTPException(404)
    return FileResponse(Path('dist/search-data') / asset)


@app.get('/{asset}')
def search_ui_asset(asset: str):
    if asset not in ('search.css', 'search.js', 'search-worker.js'):
        raise HTTPException(404)
    return FileResponse(asset)


@app.get('/api/search/')
def search_info():
    from search_preview import proxy_search
    return proxy_search()


@app.post('/api/search/')
def search_query(payload: dict):
    from search_preview import proxy_search
    return proxy_search(payload)
