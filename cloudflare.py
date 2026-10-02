"""Cloudflare authentication and D1/Vectorize API operations."""
import json
import os
from pathlib import Path
import subprocess
import time
import requests
from config import ROOT, load_jsonc


def newsletter_config():
    worker = load_jsonc(ROOT / 'workers/newsletter-test/wrangler.jsonc')
    return dict(account_id=worker['account_id'], database_id=worker['d1_databases'][0]['database_id'])


class Cloudflare:
    def __init__(self, config):
        self.config = config
        token = os.environ.get('CLOUDFLARE_API_TOKEN')
        if not token:
            result = subprocess.run([str(ROOT/'node_modules/.bin/wrangler'), 'auth', 'token', '--json'],
                                    cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError('Cloudflare authentication failed; run wrangler login or configure CLOUDFLARE_API_TOKEN')
            token = json.loads(result.stdout).get('token')
            if not token:
                raise RuntimeError('Cloudflare authentication did not return a token')
        self.session = requests.Session()
        self.session.headers['Authorization'] = f'Bearer {token}'
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{config['account_id']}"
        self.written = 0
        self.last_mutation = None

    def api(self, method, path, **kwargs):
        for attempt in range(4):
            try:
                response = self.session.request(method, self.base+path, timeout=60, **kwargs)
                data = response.json()
                if response.ok and data.get('success'):
                    return data['result']
                if response.status_code != 429 and response.status_code < 500:
                    raise RuntimeError(f"Cloudflare {path} failed: {data.get('errors')}")
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 3:
                    raise
            if attempt == 3:
                raise RuntimeError(f'Cloudflare request failed: {path}')
            time.sleep(2 ** attempt)

    def sql(self, sql, params=()):
        result = self.api('POST', f"/d1/database/{self.config['database_id']}/query", json={'sql': sql, 'params': list(params)})
        if not all(item.get('success') for item in result):
            raise RuntimeError('D1 statements failed')
        self.written += sum(item.get('meta', {}).get('rows_written', 0) for item in result)
        return result

    def upsert(self, rows):
        # Keep batches under API payload limits, with several vectors per request.
        for offset in range(0, len(rows), 2000):
            payload = '\n'.join(json.dumps(v, separators=(',', ':')) for v in rows[offset:offset+2000]) + '\n'
            result = self.api('POST', f"/vectorize/v2/indexes/{self.config['index_name']}/upsert",
                     files={'vectors': ('vectors.ndjson', payload, 'application/x-ndjson')})
            self.last_mutation = result.get('mutationId')

    def delete_vectors(self, ids):
        for offset in range(0, len(ids), 100):
            result = self.api('POST', f"/vectorize/v2/indexes/{self.config['index_name']}/delete_by_ids", json={'ids': ids[offset:offset+100]})
            self.last_mutation = result.get('mutationId')


    def d1_query(self, sql, params=()):
        return self.sql(sql, params)[0]['results']


def d1_query(sql, params=()):
    return Cloudflare(newsletter_config()).d1_query(sql, params)
