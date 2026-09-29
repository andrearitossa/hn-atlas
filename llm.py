"""One small JSON request path for topic naming and quality review."""
import json
import os
import time

import requests
import config

NAMER = 'gpt-6-luna'
URL = 'https://api.openai.com/v1/chat/completions'


def ask_json(prompt, attempts=5, model=NAMER, reasoning_effort=None, timeout=120):
    key = os.environ['OPENAI_API_KEY']
    for attempt in range(attempts):
        try:
            response = requests.post(URL,
                headers={'Authorization': f'Bearer {key}'},
                json={'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                      'response_format': {'type': 'json_object'},
                      **({'reasoning_effort':reasoning_effort} if reasoning_effort else {})}, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as error:
            if attempt + 1 >= attempts:
                raise
            print(f"Transient API {type(error).__name__}; retry {attempt+1}", flush=True)
            time.sleep(min(30, 2 ** attempt))
            continue
        if response.ok:
            payload = response.json()
            return json.loads(payload['choices'][0]['message']['content'])
        if response.status_code != 429 and response.status_code < 500:
            response.raise_for_status()
        if attempt + 1 < attempts:
            delay = min(60, 2 ** attempt)
            if response.status_code == 429:
                # Short 1/2/4/8-second retries can all land in the same token
                # limit window during a backlog. Respect the server's delay.
                try:
                    delay = max(30, float(response.headers.get('retry-after', 0)), delay)
                except (TypeError, ValueError):
                    delay = max(30, delay)
                print(f'API rate limit; retry {attempt+1} in {delay:g}s', flush=True)
            time.sleep(delay)
    response.raise_for_status()
