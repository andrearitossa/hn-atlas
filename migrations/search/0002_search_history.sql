-- Search impressions are independent of the rolling post corpus.
CREATE TABLE IF NOT EXISTS searches (
 id TEXT PRIMARY KEY,
 created_at INTEGER NOT NULL,
 query TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('hybrid','pattern','semantic')),
 topic_id INTEGER,
 sort TEXT NOT NULL CHECK(sort IN ('relevance','newest','points')),
 ranking_version TEXT NOT NULL,
 corpus_since INTEGER NOT NULL,
 corpus_until INTEGER NOT NULL,
 status INTEGER NOT NULL,
 latency_ms INTEGER NOT NULL,
 word_candidates INTEGER NOT NULL,
 semantic_candidates INTEGER NOT NULL,
 word_failed INTEGER NOT NULL,
 semantic_failed INTEGER NOT NULL,
 results_json TEXT NOT NULL CHECK(json_valid(results_json)),
 error_code TEXT
);
CREATE INDEX IF NOT EXISTS searches_created_at ON searches(created_at);
