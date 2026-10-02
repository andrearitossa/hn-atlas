CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT, time INTEGER NOT NULL,
 score INTEGER NOT NULL DEFAULT 0, descendants INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS posts_time ON posts(time);
CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS post_topics(id INTEGER NOT NULL, topic INTEGER NOT NULL, PRIMARY KEY(id,topic));
CREATE INDEX IF NOT EXISTS post_topics_topic ON post_topics(topic,id);
CREATE TABLE IF NOT EXISTS search_state(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(title,url,content='posts',content_rowid='id',tokenize="unicode61 tokenchars '+#'");
CREATE TRIGGER IF NOT EXISTS posts_insert AFTER INSERT ON posts BEGIN
 INSERT INTO posts_fts(rowid,title,url) VALUES(new.id,new.title,new.url);
END;
CREATE TRIGGER IF NOT EXISTS posts_delete AFTER DELETE ON posts BEGIN
 INSERT INTO posts_fts(posts_fts,rowid,title,url) VALUES('delete',old.id,old.title,old.url);
 DELETE FROM post_topics WHERE id=old.id;
END;
CREATE TRIGGER IF NOT EXISTS posts_text_update AFTER UPDATE OF title,url ON posts
 WHEN old.title IS NOT new.title OR old.url IS NOT new.url BEGIN
 INSERT INTO posts_fts(posts_fts,rowid,title,url) VALUES('delete',old.id,old.title,old.url);
 INSERT INTO posts_fts(rowid,title,url) VALUES(new.id,new.title,new.url);
END;
