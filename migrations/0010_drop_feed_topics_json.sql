-- feed_user_topics is the only topic store, so drop the stale deprecated copy.
ALTER TABLE feed_users DROP COLUMN topics_json;
-- Reading history is retained, so nothing scans feed_story_state by expiry.
DROP INDEX IF EXISTS feed_state_expiry;
