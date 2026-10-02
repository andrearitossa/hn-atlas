"""Serve the local UI and proxy Search to its deployed Cloudflare backend."""
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
import requests

LIVE = 'https://hackeratlas.pages.dev'
app = FastAPI(docs_url=None, redoc_url=None)


def proxy_search(payload=None):
    try:
        if payload is None:
            response = requests.get(LIVE+'/api/search/', timeout=25)
        else:
            response = requests.post(LIVE+'/api/search/', headers={'Origin': LIVE}, json=payload, timeout=25)
        return JSONResponse(response.json(), status_code=response.status_code)
    except (requests.RequestException, ValueError):
        raise HTTPException(503, 'Search is unavailable. Try again shortly.')


@app.get('/api/search/')
def search_info():
    return proxy_search()


@app.post('/api/search/')
def search(payload: dict, request: Request):
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403)
    return proxy_search(payload)


@app.get('/{path:path}')
def static(path: str, request: Request):
    root = Path('dist').resolve()
    # Preview the current Search source without rebuilding the topic archive.
    if path in ('search/', 'search', 'search/index.html'):
        return RedirectResponse('/' + ('?' + request.url.query if request.url.query else ''), status_code=301)
    if path in ('search.js', 'search.css'):
        return FileResponse(path)
    file = (root / path).resolve()
    if not file.is_relative_to(root):
        raise HTTPException(404)
    if file.is_dir():
        file /= 'index.html'
    if not file.is_file():
        raise HTTPException(404)
    return FileResponse(file, headers={'Cache-Control': 'no-store'} if file.suffix == '.html' else None)
