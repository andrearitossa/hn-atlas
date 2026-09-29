"""Stored two-component Gaussian topic classifier; no fitting or API calls at inference."""
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
from scipy.special import logsumexp, softmax

MODEL_PATH = Path(__file__).resolve().parent / 'models' / 'model.npz'
THRESHOLD = .70
MAX_LABELS = 3
FORMAT_VERSION = 1


class Classifier:
    def __init__(self, arrays):
        self.classes = np.asarray(arrays['classes'], dtype=np.int64)
        self.means = np.asarray(arrays['means'], dtype=np.float64)
        self.variances = np.asarray(arrays['variances'], dtype=np.float64)
        self.weights = np.asarray(arrays['weights'], dtype=np.float64)
        self.priors = np.asarray(arrays['priors'], dtype=np.float64)
        self.metadata = json.loads(str(arrays['metadata']))
        self.temperature = float(self.metadata['temperature'])
        self.version = self.metadata['model_id']
        if (self.metadata['format_version'] != FORMAT_VERSION or len(self.classes) < 2
                or len(set(self.classes)) != len(self.classes) or (self.classes < 0).any()
                or self.means.ndim != 3 or self.means.shape[:2] != (len(self.classes), 2)
                or self.variances.shape != self.means.shape
                or self.weights.shape != self.means.shape[:2] or self.priors.shape != self.classes.shape
                or not np.isfinite(self.temperature) or self.temperature <= 0):
            raise ValueError('Invalid topic classifier artifact')
        for a in (self.means, self.variances, self.weights, self.priors):
            if not np.isfinite(a).all():
                raise ValueError('Nonfinite topic classifier parameters')
        if ((self.variances <= 0).any() or (self.weights <= 0).any() or (self.priors <= 0).any()
                or not np.allclose(self.weights.sum(axis=1), 1) or not np.isclose(self.priors.sum(), 1)):
            raise ValueError('Invalid Gaussian parameters')
        self.precision = 1 / self.variances
        self.linear = self.means * self.precision
        self.constant = (np.log(self.weights) - .5 * (self.means.shape[2] * np.log(2 * np.pi)
                          + np.log(self.variances).sum(axis=2)
                          + (self.means * self.linear).sum(axis=2)))

    def probabilities(self, vectors):
        x = np.asarray(vectors, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != self.means.shape[2] or not np.isfinite(x).all():
            raise ValueError('Invalid story embeddings for topic classifier')
        if not len(x):
            return np.empty((0, len(self.classes)))
        component = (-.5 * (x*x) @ self.precision.reshape(-1, x.shape[1]).T
                     + x @ self.linear.reshape(-1, x.shape[1]).T
                     + self.constant.ravel())
        scores = logsumexp(component.reshape(len(x), len(self.classes), 2), axis=2) + np.log(self.priors)
        return softmax(scores / self.temperature, axis=1)

    def predict(self, vectors):
        probabilities = self.probabilities(vectors)
        count = min(MAX_LABELS, len(self.classes))
        rank = np.argsort(-probabilities, axis=1, kind='stable')[:, :count]
        p = np.take_along_axis(probabilities, rank, axis=1)
        sizes = np.minimum(count, 1 + (np.cumsum(p, axis=1) < THRESHOLD).sum(axis=1))
        return [(self.classes[r[:n]].tolist(), scores[:n].tolist(), bool(scores[:n].sum() >= THRESHOLD))
                for r, scores, n in zip(rank, p, sizes)]


@lru_cache(maxsize=2)
def _load(path, mtime, size):
    with np.load(path, allow_pickle=False) as artifact:
        return Classifier(dict(artifact))


def load(path=None):
    path = Path(path or MODEL_PATH)
    stat = path.stat()
    return _load(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def classify(conn, now):
    """Assign previously unfiled, unqueued stories; preserve editorial overrides."""
    from core import unit
    from topics import iter_vectors, MODEL_PATH as PROTOTYPE_PATH
    classifier = load()
    active = {r[0] for r in conn.execute('SELECT id FROM topic_registry')}
    if set(classifier.classes) != active:
        raise ValueError('Retrain topic_classifier: active topic IDs differ from the stored model')
    # Keep historical similarity diagnostics comparable for monthly maintenance.
    with np.load(PROTOTYPE_PATH, allow_pickle=False) as artifact:
        model = dict(artifact)
    edits = dict(conn.execute('SELECT id,topic FROM editorial_decisions')) if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='editorial_decisions'").fetchone() else {}
    where = ('s.dead=0 AND s.deleted=0 AND e.input_version=2 '
             'AND e.id NOT IN (SELECT id FROM story_topics) '
             'AND e.id NOT IN (SELECT id FROM classification_queue)')
    filed = 0
    for ids, vectors in iter_vectors(conn, where, size=1000):
        selections = classifier.predict(vectors)
        similarity = unit(vectors - model['mean']) @ model['prototype_centroids'].T
        for pos, (ident, (labels, probabilities, reached)) in enumerate(zip(ids, selections)):
            manual = edits.get(ident)
            if manual in active:
                labels, probabilities, reached = [manual], [1.0], True
            primary = labels[0]
            own = model['prototype_topic_ids'] == primary
            fit = float(similarity[pos, own].max()) if own.any() else 0.
            margin = fit - float(similarity[pos, ~own].max())
            conn.execute('INSERT INTO story_topics(id,topic,sim,margin) VALUES (?,?,?,?)',
                         (ident, primary, fit, margin))
            conn.executemany('INSERT INTO story_topic_labels(id,topic,rank,probability,model_version,assigned_at,target_reached) VALUES (?,?,?,?,?,?,?)',
                [(ident, topic, rank, probability, 'editorial' if manual in active else classifier.version, now, int(reached))
                 for rank, (topic, probability) in enumerate(zip(labels, probabilities), 1)])
            filed += 1
    return filed
