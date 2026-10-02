"""Paths and environment for the single local Atlas installation."""
import os
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for line in (ROOT / '.env').read_text().splitlines() if (ROOT / '.env').exists() else ():
    key, separator, value = line.strip().partition('=')
    if separator and key and not key.startswith('#'):
        os.environ.setdefault(key, value.strip().strip("'\""))

DB = ROOT / 'data/atlas.db'
MODEL_PATH = ROOT / 'data/topic_model.npz'


def load_jsonc(path):
    text = Path(path).read_text()
    strings = r'"(?:\\.|[^"\\])*"'
    text = re.sub(strings + r'|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else '\n' * m[0].count('\n'), text)
    text = re.sub(strings + r'|,\s*(?=[}\]])',
                  lambda m: m[0] if m[0].startswith('"') else '', text)
    return json.loads(text)


def admin_email():
    email = os.environ.get('ADMIN_EMAIL', '').strip()
    if not email:
        raise RuntimeError('ADMIN_EMAIL is required')
    return email
