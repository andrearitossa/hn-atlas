CREATE TABLE IF NOT EXISTS newsletter_clicks (
 topic INTEGER NOT NULL, edition INTEGER NOT NULL, story INTEGER NOT NULL,
 clicks INTEGER NOT NULL DEFAULT 0, last_clicked_at INTEGER NOT NULL,
 PRIMARY KEY(topic,edition,story)
);
