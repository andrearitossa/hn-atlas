CREATE TABLE IF NOT EXISTS newsletter_subscriptions (
 id TEXT PRIMARY KEY, email TEXT NOT NULL COLLATE NOCASE, topic INTEGER NOT NULL,
 created_at INTEGER NOT NULL, unsubscribed_at INTEGER,
 legacy_tokens TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(legacy_tokens)),
 UNIQUE(email,topic)
);
CREATE TABLE IF NOT EXISTS newsletter_issues (
 topic INTEGER NOT NULL, edition INTEGER NOT NULL, prepared_at INTEGER NOT NULL,
 source_as_of INTEGER NOT NULL, subject TEXT NOT NULL, html TEXT NOT NULL,
 posts TEXT NOT NULL, sent_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(topic,edition)
);
CREATE TABLE IF NOT EXISTS newsletter_deliveries (
 topic INTEGER NOT NULL, edition INTEGER NOT NULL, subscription TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('sending','sent','failed')),
 message_id TEXT, sent_at INTEGER, error TEXT,
 PRIMARY KEY(topic,edition,subscription)
);
CREATE TABLE IF NOT EXISTS newsletter_clicks (
 topic INTEGER NOT NULL, edition INTEGER NOT NULL, story INTEGER NOT NULL,
 clicks INTEGER NOT NULL DEFAULT 0, last_clicked_at INTEGER NOT NULL,
 PRIMARY KEY(topic,edition,story)
);
