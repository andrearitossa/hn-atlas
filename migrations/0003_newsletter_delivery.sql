CREATE TABLE IF NOT EXISTS newsletter_test_subscriptions (
 token TEXT PRIMARY KEY, email TEXT NOT NULL, topic INTEGER NOT NULL, name TEXT NOT NULL,
 starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
 UNIQUE(email,topic,starts_at)
);
CREATE TABLE IF NOT EXISTS newsletter_test_issues (
 subscription TEXT NOT NULL, edition INTEGER NOT NULL, due_at INTEGER NOT NULL,
 prepared_at INTEGER NOT NULL, source_as_of INTEGER NOT NULL, subject TEXT NOT NULL,
 body TEXT NOT NULL, posts TEXT NOT NULL, html TEXT,
 state TEXT NOT NULL DEFAULT 'ready', message_id TEXT, sent_at INTEGER, error TEXT,
 PRIMARY KEY(subscription,edition)
);
CREATE TABLE IF NOT EXISTS newsletter_test_checks (
 at INTEGER PRIMARY KEY, sent INTEGER NOT NULL, failures INTEGER NOT NULL, pending INTEGER NOT NULL
);
