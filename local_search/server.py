"""Run with python -m local_search.server; never included in the public app."""
import argparse
import logging
import os
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
import requests
import uvicorn

from config import DB
from database import connect
from local_search.search import query_vector, search

app = FastAPI(title='Local Atlas semantic search', docs_url=None, redoc_url=None)


@app.middleware('http')
async def local_only(request: Request, call_next):
    # Also reject remote Host/Origin values to prevent browser DNS rebinding.
    if request.client.host not in ('127.0.0.1', '::1', 'testclient') or request.url.hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver'):
        return JSONResponse({'detail': 'Local access only'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail': 'Same-origin access only'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def index():
    return FileResponse(Path(__file__).with_name('index.html'))


@app.get('/api/topics')
def topics():
    with connect(DB, readonly=True) as conn:
        return [dict(r) for r in conn.execute('SELECT t.id,t.name,t.description FROM topics t JOIN topic_registry r USING(id) ORDER BY t.name')]


@app.get('/api/search')
def semantic_search(q: Annotated[str, Query(min_length=1, max_length=500)],
                    topic: int | None = None,
                    weight: Annotated[float, Query(ge=0, le=0.25)] = 0.15,
                    limit: Annotated[int, Query(ge=1, le=100)] = 30):
    q = q.strip()
    if not q:
        raise HTTPException(422, 'Enter a search query')
    if not os.environ.get('OPENAI_API_KEY'):
        raise HTTPException(503, 'Set OPENAI_API_KEY in .env to embed search queries')
    try:
        with connect(DB, readonly=True) as conn:
            if topic is not None and not conn.execute('SELECT 1 FROM topics JOIN topic_registry USING(id) WHERE id=?', (topic,)).fetchone():
                raise HTTPException(404, 'Topic not found')
            result = search(conn, query_vector(q), topic_id=topic, topic_weight=weight, limit=limit)
            result['live_stories'] = conn.execute('SELECT count(*) FROM stories WHERE dead=0 AND deleted=0 AND title IS NOT NULL').fetchone()[0]
            return result
    except (requests.RequestException, ValueError, sqlite3.Error):
        logging.exception('Local semantic search failed')
        raise HTTPException(503, 'Search unavailable; check the local server log')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(app, host='127.0.0.1', port=args.port)
