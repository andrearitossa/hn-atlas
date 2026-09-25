"""One small JSON request path for topic naming and quality review."""
import json
import os
import time

import requests
import api_usage

NAMER = 'gpt-5.6-terra'
URL = 'https://api.openai.com/v1/chat/completions'


def ask_json(prompt, attempts=5, model=NAMER, reasoning_effort=None, timeout=120):
    key = os.environ['OPENAI_API_KEY']
    for attempt in range(attempts):
        ticket = api_usage.reserve(model, [prompt], api_usage.MAX_OUTPUT)
        try:
            response = requests.post(URL,
                headers={'Authorization': f'Bearer {key}'},
                json={'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                      'response_format': {'type': 'json_object'},
                      **({'reasoning_effort':reasoning_effort} if reasoning_effort else {}),
                      **api_usage.completion_options()}, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as error:
            if attempt + 1 >= attempts:
                raise
            print(f"Transient API {type(error).__name__}; retry {attempt+1}", flush=True)
            time.sleep(min(30, 2 ** attempt))
            continue
        if response.ok:
            payload = response.json()
            api_usage.finish(ticket, payload)
            return json.loads(payload['choices'][0]['message']['content'])
        if response.status_code != 429 and response.status_code < 500:
            response.raise_for_status()
        if attempt + 1 < attempts:
            time.sleep(min(30, 2 ** attempt))
    response.raise_for_status()
