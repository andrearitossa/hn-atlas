"""Shared vector conventions for the topic map."""
import numpy as np

SEED = 0


def unit(v):
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(norm, 1e-12)
