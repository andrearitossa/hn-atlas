CREATE TABLE IF NOT EXISTS newsletter_signups (
    email TEXT NOT NULL,
    topic_id INTEGER NOT NULL,
    topic_name TEXT NOT NULL,
    PRIMARY KEY (email, topic_id)
);
