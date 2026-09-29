-- Consolidate the old newsletter tables into one subscription registry,
-- shared issue content, and per-recipient deliveries.
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

INSERT OR IGNORE INTO newsletter_subscriptions(id,email,topic,created_at,unsubscribed_at,legacy_tokens)
SELECT (SELECT x.token FROM newsletter_test_subscriptions x
        WHERE lower(x.email)=lower(s.email) AND x.topic=s.topic
        ORDER BY x.starts_at,x.token LIMIT 1),
       lower(s.email),s.topic,min(s.starts_at),
       CASE WHEN max(s.cancelled) THEN max(s.starts_at) END,
       json_group_array(s.token)
FROM newsletter_test_subscriptions s GROUP BY lower(s.email),s.topic;

INSERT OR IGNORE INTO newsletter_subscriptions(id,email,topic,created_at)
SELECT lower(hex(randomblob(16))),lower(email),topic_id,unixepoch()
FROM newsletter_signups;

-- One immutable content record per topic edition; choose the earliest draft.
INSERT OR IGNORE INTO newsletter_issues(topic,edition,prepared_at,source_as_of,subject,html,posts)
SELECT topic,edition,prepared_at,source_as_of,subject,
       coalesce(html,'<pre>' || replace(replace(replace(body,'&','&amp;'),'<','&lt;'),'>','&gt;') || '</pre>'),
       coalesce((SELECT json_group_array(json(value)) FROM (
         SELECT DISTINCT j.value FROM newsletter_test_issues old
         JOIN newsletter_test_subscriptions old_sub ON old_sub.token=old.subscription
         JOIN json_each(old.posts) j
         WHERE old_sub.topic=chosen.topic AND old.edition=chosen.edition
       )), '[]')
FROM (
 SELECT s.topic,i.edition,i.prepared_at,i.source_as_of,i.subject,i.html,i.body,i.posts,
        row_number() OVER (PARTITION BY s.topic,i.edition ORDER BY i.prepared_at,s.token) AS rank
 FROM newsletter_test_issues i JOIN newsletter_test_subscriptions s ON s.token=i.subscription
) chosen WHERE rank=1;

-- Duplicate legacy tokens collapse to one recipient delivery. A send wins
-- over an ambiguous attempt, which wins over a known failure.
INSERT OR IGNORE INTO newsletter_deliveries(topic,edition,subscription,state,message_id,sent_at,error)
SELECT topic,edition,subscription,state,message_id,sent_at,error
FROM (
 SELECT s.topic,i.edition,n.id AS subscription,i.state,i.message_id,i.sent_at,i.error,
        row_number() OVER (PARTITION BY s.topic,i.edition,n.id ORDER BY
          CASE i.state WHEN 'sent' THEN 0 WHEN 'sending' THEN 1 ELSE 2 END,
          i.prepared_at,s.token) AS rank
 FROM newsletter_test_issues i
 JOIN newsletter_test_subscriptions s ON s.token=i.subscription
 JOIN newsletter_subscriptions n ON n.email=lower(s.email) AND n.topic=s.topic
 WHERE i.state IN ('sent','sending','failed')
) WHERE rank=1;

UPDATE newsletter_issues SET sent_count=(
 SELECT count(*) FROM newsletter_deliveries d WHERE d.topic=newsletter_issues.topic
 AND d.edition=newsletter_issues.edition AND d.state='sent');

DROP TABLE newsletter_test_checks;
DROP TABLE newsletter_test_issues;
DROP TABLE newsletter_test_subscriptions;
DROP TABLE newsletter_signups;
