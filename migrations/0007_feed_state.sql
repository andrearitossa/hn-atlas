CREATE TABLE IF NOT EXISTS feed_catalog (
 id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE IF NOT EXISTS feed_story_state (
 user_id TEXT NOT NULL REFERENCES feed_users(id) ON DELETE CASCADE,
 article_key TEXT NOT NULL CHECK(length(article_key)=64),
 seen_at INTEGER NOT NULL, opened_at INTEGER,
 PRIMARY KEY(user_id,article_key)
);
CREATE INDEX IF NOT EXISTS feed_state_expiry ON feed_story_state(seen_at);
