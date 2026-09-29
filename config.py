"""Paths and environment for the single local Atlas installation."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for line in (ROOT / '.env').read_text().splitlines() if (ROOT / '.env').exists() else ():
    key, separator, value = line.strip().partition('=')
    if separator and key and not key.startswith('#'):
        os.environ.setdefault(key, value.strip().strip("'\""))

DB = ROOT / 'data/atlas.db'
MODEL_PATH = ROOT / 'data/topic_model.npz'
