"""Shared vector conventions for the topic map."""
from datetime import datetime, timezone

import numpy as np

SEED = 0
AMBIG = 0.02


def ts(year):
    return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())


def unit(v):
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(norm, 1e-12)
