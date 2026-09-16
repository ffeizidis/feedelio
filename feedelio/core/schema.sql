PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS folders (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT REFERENCES folders(id),
 CHECK (id != parent_id)
);
INSERT OR IGNORE INTO folders VALUES ('inbox', 'Unfiled', NULL);
CREATE TABLE IF NOT EXISTS feeds (
 id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, title TEXT NOT NULL, folder_id TEXT NOT NULL REFERENCES folders(id),
 site_url TEXT NOT NULL DEFAULT '', options TEXT NOT NULL DEFAULT '{}', tags TEXT NOT NULL DEFAULT '[]',
 created TEXT NOT NULL, checked TEXT, next_check TEXT, last_article TEXT, error TEXT,
 failures INTEGER NOT NULL DEFAULT 0, interval INTEGER NOT NULL DEFAULT 60, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS articles (
 id TEXT PRIMARY KEY, feed_id TEXT NOT NULL REFERENCES feeds(id), guid TEXT NOT NULL,
 url TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', published TEXT NOT NULL,
 added TEXT NOT NULL, body TEXT NOT NULL, text TEXT NOT NULL, transcript TEXT NOT NULL DEFAULT '',
 enclosures TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]',
 read INTEGER NOT NULL DEFAULT 0, starred INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
 duplicate INTEGER NOT NULL DEFAULT 0, read_at TEXT, words INTEGER NOT NULL DEFAULT 0,
 fingerprint TEXT NOT NULL, extracted INTEGER NOT NULL DEFAULT 0, state_changed TEXT,
 UNIQUE(feed_id, guid)
);
CREATE INDEX IF NOT EXISTS articles_feed ON articles(feed_id, published);
CREATE INDEX IF NOT EXISTS articles_stream ON articles(deleted, read, published);
CREATE INDEX IF NOT EXISTS articles_url ON articles(feed_id, url);
CREATE INDEX IF NOT EXISTS articles_fingerprint ON articles(feed_id, fingerprint);
CREATE VIRTUAL TABLE IF NOT EXISTS article_search USING fts5(title, text, transcript, content=articles, content_rowid=rowid, tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
 INSERT INTO article_search(rowid,title,text,transcript) VALUES(new.rowid,new.title,new.text,new.transcript);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
 INSERT INTO article_search(article_search,rowid,title,text,transcript) VALUES('delete',old.rowid,old.title,old.text,old.transcript);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE OF title,text,transcript ON articles BEGIN
 INSERT INTO article_search(article_search,rowid,title,text,transcript) VALUES('delete',old.rowid,old.title,old.text,old.transcript);
 INSERT INTO article_search(rowid,title,text,transcript) VALUES(new.rowid,new.title,new.text,new.transcript);
END;
CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, name TEXT NOT NULL, field TEXT NOT NULL,
 pattern TEXT NOT NULL, action TEXT NOT NULL, value TEXT NOT NULL DEFAULT '', feed_id TEXT REFERENCES feeds(id), enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', created TEXT NOT NULL, updated TEXT NOT NULL, error TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created);
CREATE TABLE IF NOT EXISTS undo (id TEXT PRIMARY KEY, label TEXT NOT NULL, snapshot TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, article_id TEXT NOT NULL, feed_id TEXT NOT NULL,
 kind TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tombstones (feed_id TEXT NOT NULL, guid TEXT NOT NULL, PRIMARY KEY(feed_id,guid));
CREATE TABLE IF NOT EXISTS chrome_state (url TEXT PRIMARY KEY, chrome_updated INTEGER NOT NULL);
