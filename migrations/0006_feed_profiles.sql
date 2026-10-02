PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS feed_users (
 id TEXT PRIMARY KEY, email TEXT NOT NULL COLLATE NOCASE UNIQUE,
 created_at INTEGER NOT NULL, verified_at INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 topics_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(topics_json))
);
CREATE TABLE IF NOT EXISTS feed_login_tokens (
 token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at INTEGER NOT NULL,
 consumed_at INTEGER, consumed_by TEXT
);
CREATE INDEX IF NOT EXISTS feed_login_expiry ON feed_login_tokens(expires_at);
CREATE TABLE IF NOT EXISTS feed_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES feed_users(id) ON DELETE CASCADE,
 expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS feed_session_user ON feed_sessions(user_id);
CREATE INDEX IF NOT EXISTS feed_session_expiry ON feed_sessions(expires_at);
CREATE TABLE IF NOT EXISTS feed_user_topics (
 user_id TEXT NOT NULL REFERENCES feed_users(id) ON DELETE CASCADE,
 topic_id INTEGER NOT NULL, PRIMARY KEY(user_id,topic_id)
);
CREATE TABLE IF NOT EXISTS feed_visits (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES feed_users(id) ON DELETE CASCADE,
 edition TEXT NOT NULL, ranking_version TEXT NOT NULL,
 topics_json TEXT NOT NULL CHECK(json_valid(topics_json)),
 stories_json TEXT NOT NULL CHECK(json_valid(stories_json)), created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS feed_visit_user ON feed_visits(user_id);
CREATE INDEX IF NOT EXISTS feed_visit_time ON feed_visits(created_at);
CREATE TABLE IF NOT EXISTS feed_events (
 user_id TEXT NOT NULL REFERENCES feed_users(id) ON DELETE CASCADE, event_id TEXT NOT NULL,
 visit_id TEXT NOT NULL REFERENCES feed_visits(id) ON DELETE CASCADE,
 type TEXT NOT NULL CHECK(type IN ('visible','article_opened','hn_vote_link_opened')),
 story_id INTEGER NOT NULL, position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 60),
 created_at INTEGER NOT NULL, PRIMARY KEY(user_id,event_id)
);
CREATE INDEX IF NOT EXISTS feed_event_time ON feed_events(created_at);
CREATE TABLE IF NOT EXISTS feed_rate_limits (
 key TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS feed_rate_expiry ON feed_rate_limits(expires_at);

CREATE TRIGGER IF NOT EXISTS feed_topics_changed AFTER UPDATE OF topics_json ON feed_users
BEGIN
 DELETE FROM feed_user_topics WHERE user_id=NEW.id;
 INSERT INTO feed_user_topics(user_id,topic_id) SELECT NEW.id,value FROM json_each(NEW.topics_json);
END;
