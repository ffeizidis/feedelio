"""Application boundary. API, MCP and worker depend on this package only.

reader owns feed transport/parsing/raw-entry persistence. The application DB is a
transactional projection containing processed content and user-owned state.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import math
import os
import sqlite3
import statistics
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import regex
from bs4 import BeautifulSoup
from defusedxml.ElementTree import fromstring
from markdownify import markdownify
from rapidfuzz.fuzz import ratio
from reader import make_reader
from reader._parser.jsonfeed import JSONFeedParser
from reader.discover import from_http_response
from requests.adapters import HTTPAdapter

from .content import (
    clean_url,
    embed_url,
    extract,
    fetch,
    fingerprint,
    local_frame_origin,
    plain,
    safe_url,
    sanitize,
    text_html,
)
from .reader_extensions import BoundedParser, ExtendedFeedparser


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


DEFAULTS = dict(
    theme="system",
    font="serif",
    font_size=19,
    line_height=1.7,
    width=65,
    images=False,
    hide_read=False,
    sort="newest",
    deduplicate=True,
    retention_days=90,
    invidious="",
    rsshub="https://rsshub.app",
)
JSON_FIELDS = {"options", "tags", "enclosures", "payload", "result"}


def record(row):
    result = dict(row)
    for key in JSON_FIELDS & result.keys():
        if result[key] is not None:
            result[key] = json.loads(result[key])
    return result


class GuardAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        safe_url(request.url)  # Requests also calls this adapter for every redirect.
        if not kwargs.get("timeout"):
            kwargs["timeout"] = (5, 30)
        return super().send(request, **kwargs)


class Core:
    def __init__(self, data_dir=None):
        self.root = Path(data_dir or os.getenv("FEEDELIO_DATA", "data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "downloads").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "app.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.create_function(
            "title_similarity", 2, lambda a, b: ratio(a.casefold(), b.casefold()), deterministic=True
        )
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(Path(__file__).with_name("schema.sql").read_text())
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def rows(self, sql, params=()):
        return [record(row) for row in self.db.execute(sql, params)]

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        if row is None:
            raise ValueError("Item not found.")
        return record(row)

    @contextmanager
    def reader(self, options=None, moved=None):
        options = options or {}

        def plugin(reader):
            @reader._parser.lazy_init
            def configure(parser):
                http = parser.get_retriever("http://")
                for mime, parsers in parser.parsers_by_mime_type.items():
                    parser.parsers_by_mime_type[mime] = [(q, BoundedParser(p)) for q, p in parsers]
                http.session.mount("http://", GuardAdapter())
                http.session.mount("https://", GuardAdapter())
                http.session.headers["User-Agent"] = options.get("user_agent") or "Feedelio/0.1"
                if options.get("cookie"):
                    http.session.headers["Cookie"] = options["cookie"]
                if options.get("proxy"):
                    http.session.proxies = dict(http=options["proxy"], https=options["proxy"])

                def response_hook(session, response, request, **kwargs):
                    if (
                        moved is not None
                        and response.history
                        and all(r.status_code in (301, 308) for r in response.history)
                    ):
                        moved.append(response.url)

                http.response_hooks.append(response_hook)

        with make_reader(str(self.root / "reader.sqlite"), plugins=[plugin], search_enabled=False) as reader:
            yield reader

    def settings(self):
        return DEFAULTS | {
            r["key"]: json.loads(r["value"])
            for r in self.rows("SELECT * FROM settings")
            if r["key"] in DEFAULTS
        }

    def save_settings(self, values):
        if set(values) - DEFAULTS.keys():
            raise ValueError("Unknown setting.")
        merged = self.settings() | values
        if merged["theme"] not in ("system", "light", "dark") or merged["font"] not in (
            "serif",
            "sans",
            "mono",
        ):
            raise ValueError("Invalid theme or font.")
        for key, lo, hi in [
            ("font_size", 16, 32),
            ("line_height", 1.4, 2.2),
            ("width", 45, 75),
            ("retention_days", 0, 36500),
        ]:
            if not isinstance(merged[key], (int, float)) or not lo <= merged[key] <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}.")
        if merged["invidious"]:
            safe_url(merged["invidious"])
            local_frame_origin(merged["invidious"])
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO settings VALUES (?,?)",
                [(k, json.dumps(v)) for k, v in values.items()],
            )
        return self.settings()

    def frame_sources(self):
        try:
            origin = local_frame_origin(self.settings()["invidious"])
        except ValueError:
            origin = ""
        return "https:" + (" " + origin if origin else "")

    def folders(self):
        return self.rows("SELECT * FROM folders ORDER BY name COLLATE NOCASE")

    def descendants(self, folder_id):
        return [
            r["id"]
            for r in self.rows(
                """WITH RECURSIVE tree(id) AS (
          SELECT id FROM folders WHERE id=? UNION ALL SELECT f.id FROM folders f JOIN tree t ON f.parent_id=t.id)
          SELECT id FROM tree""",
                (folder_id,),
            )
        ]

    def save_folder(self, name, parent_id=None, id=None):
        if not name.strip():
            raise ValueError("A folder needs a name.")
        if id == "inbox":
            raise ValueError("Unfiled is the permanent default folder.")
        if parent_id:
            self.one("SELECT id FROM folders WHERE id=?", (parent_id,))
        if id and parent_id in self.descendants(id):
            raise ValueError("A folder cannot contain itself or its ancestors.")
        id = id or uid()
        with self.db:
            self.db.execute(
                "INSERT INTO folders VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,parent_id=excluded.parent_id",
                (id, name.strip(), parent_id),
            )
        return {"id": id}

    def delete_folder(self, id):
        if id == "inbox":
            raise ValueError("Unfiled cannot be deleted.")
        folder = self.one("SELECT * FROM folders WHERE id=?", (id,))
        with self.db:
            self.db.execute("UPDATE feeds SET folder_id=? WHERE folder_id=?", ("inbox", id))
            self.db.execute("UPDATE folders SET parent_id=? WHERE parent_id=?", (folder["parent_id"], id))
            self.db.execute("DELETE FROM folders WHERE id=?", (id,))
        return {"ok": True}

    def feeds(self):
        feeds = self.rows("""SELECT f.*,count(CASE WHEN a.read=0 AND a.deleted=0 THEN 1 END) unread,
          count(CASE WHEN a.deleted=0 THEN 1 END) total FROM feeds f
          LEFT JOIN articles a ON a.feed_id=f.id WHERE f.deleted=0 GROUP BY f.id ORDER BY f.title COLLATE NOCASE""")
        stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        for f in feeds:
            f["health"] = (
                "error"
                if f["error"]
                else (
                    "quiet"
                    if (f["last_article"] or f["created"]) < stale
                    else "healthy"
                    if f["checked"]
                    else "pending"
                )
            )
            f["favicon"] = (
                urljoin(f["site_url"] or f["url"], "/favicon.ico") if f["url"].startswith("http") else None
            )
        return feeds

    def overview(self):
        feeds = self.feeds()
        folders = self.folders()
        for f in folders:
            scope = self.descendants(f["id"])
            f["unread"] = sum(feed["unread"] for feed in feeds if feed["folder_id"] in scope)
        counts = self.one("""SELECT count(*) total,coalesce(sum(a.read=0),0) unread,
          coalesce(sum(a.starred=1),0) starred FROM articles a JOIN feeds f ON f.id=a.feed_id
          WHERE a.deleted=0 AND f.deleted=0""")
        return dict(
            feeds=feeds,
            folders=folders,
            counts=counts,
            settings=self.settings(),
            jobs=self.rows("SELECT * FROM jobs ORDER BY created DESC LIMIT 30"),
            downloads=self.rows("SELECT * FROM jobs WHERE kind='download' ORDER BY created DESC"),
            undo=self.rows("SELECT id,label FROM undo ORDER BY created DESC LIMIT 1"),
            worker=self.rows("SELECT value FROM settings WHERE key='worker_heartbeat'"),
        )

    def discover(self, url):
        url = safe_url(url)
        data, final, headers = fetch(url)
        if urlsplit(final).hostname in ("youtube.com", "www.youtube.com"):
            soup = BeautifulSoup(data, "html.parser")
            channel = soup.select_one('meta[itemprop="channelId"]')
            match = regex.search(r'"channelId"\s*:\s*"(UC[\w-]+)"', data.decode(errors="replace"))
            channel_id = channel.get("content") if channel else match[1] if match else None
            if channel_id:
                return [
                    {
                        "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                        "title": "YouTube channel",
                    }
                ]
        links = from_http_response(final, data, headers)
        if links:
            return [{"url": link.href, "title": link.title or link.href} for link in links]
        # Validate a direct feed with the same parsers as retrieval, without
        # subscribing or fetching it a second time. MIME alone is not evidence.
        parser = JSONFeedParser() if data.lstrip().startswith(b"{") else ExtendedFeedparser()
        try:
            feed, entries = parser(final, io.BytesIO(data), headers)
            list(entries)
        except Exception as error:
            raise ValueError(
                "No feed found. Try an explicit feed URL or configure a site scraper."
            ) from error
        return [{"url": final, "title": feed.title or final}]

    def subscribe(self, url, folder_id="inbox", title="", options=None):
        safe_url(url)
        options = self.validate_options(options or {})
        with self.db:
            return self._subscribe(url, folder_id, title, options)

    def _subscribe(self, url, folder_id, title, options):
        """Write a validated subscription and job in the caller's transaction.

        The worker creates reader's cache on first refresh; import is one app-DB
        transaction and never commits a half-imported library or reader cache.
        """
        self.one("SELECT id FROM folders WHERE id=?", (folder_id,))
        existing = self.rows("SELECT id FROM feeds WHERE url=?", (url,))
        id = existing[0]["id"] if existing else uid()
        self.db.execute(
            """INSERT INTO feeds(id,url,title,folder_id,options,created)
              VALUES(?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET deleted=0,folder_id=excluded.folder_id""",
            (
                id,
                url,
                title.strip() or urlsplit(url).hostname,
                folder_id,
                json.dumps(options | {"custom_title": bool(title.strip())}),
                now(),
            ),
        )
        self._enqueue("refresh", {"feed_id": id})
        return {"id": id}

    def validate_options(self, options):
        allowed = {
            "custom_title",
            "interval",
            "archive",
            "extract",
            "selector",
            "user_agent",
            "cookie",
            "proxy",
            "scrape_selector",
            "scrape_link",
            "backfill_url",
            "retention_days",
            "transcript_selector",
        }
        if set(options) - allowed:
            raise ValueError("Unknown feed option.")
        if options.get("interval") is not None and not 5 <= int(options["interval"]) <= 10080:
            raise ValueError("Refresh interval must be 5–10080 minutes, or null for automatic.")
        for key in ("selector", "scrape_selector", "scrape_link", "transcript_selector"):
            if options.get(key):
                BeautifulSoup("", "html.parser").select(options[key])
        if options.get("backfill_url"):
            safe_url(options["backfill_url"])
        if options.get("proxy"):
            safe_url(options["proxy"])
        return options

    def edit_feed(self, id, title=None, folder_id=None, options=None, tags=None):
        f = self.one("SELECT * FROM feeds WHERE id=?", (id,))
        if folder_id:
            self.one("SELECT id FROM folders WHERE id=?", (folder_id,))
        opts = f["options"] | (options or {})
        if title is not None:
            opts["custom_title"] = bool(title.strip())
        self.validate_options(opts)
        interval = self.refresh_interval(id, opts)
        reschedule = opts.get("interval") != f["options"].get("interval")
        with self.db:
            self.db.execute(
                "UPDATE feeds SET title=?,folder_id=?,options=?,tags=? WHERE id=?",
                (
                    title.strip() or f["title"] if title is not None else f["title"],
                    folder_id or f["folder_id"],
                    json.dumps(opts),
                    json.dumps(tags if tags is not None else f["tags"]),
                    id,
                ),
            )
            if reschedule:
                self.db.execute(
                    "UPDATE feeds SET interval=?,next_check=? WHERE id=?",
                    (interval, (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(), id),
                )
        return {"ok": True}

    def bulk_feeds(self, ids, action, folder_id=None, tags=None):
        if action not in ("move", "tag", "delete", "refresh"):
            raise ValueError("Unknown bulk operation.")
        feeds = [self.one("SELECT * FROM feeds WHERE id=?", (id,)) for id in ids]
        if action == "move":
            self.one("SELECT id FROM folders WHERE id=?", (folder_id,))
        with self.db:
            undo = (
                self._undo(
                    "Delete subscriptions", [("feeds", f["id"], {"deleted": f["deleted"]}) for f in feeds]
                )
                if action == "delete"
                else None
            )
            for f in feeds:
                if action == "delete":
                    self.db.execute("UPDATE feeds SET deleted=1 WHERE id=?", (f["id"],))
                elif action == "move":
                    self.db.execute("UPDATE feeds SET folder_id=? WHERE id=?", (folder_id, f["id"]))
                elif action == "tag":
                    self.db.execute("UPDATE feeds SET tags=? WHERE id=?", (json.dumps(tags or []), f["id"]))
        if action == "refresh":
            for f in feeds:
                self.enqueue("refresh", {"feed_id": f["id"]})
        return {"ok": True, "undo": undo}

    def _where(self, view="all", feed_id=None, folder_id=None, unread=False, q="", tag=None, **ignored):
        where, args = ["a.deleted=0", "f.deleted=0"], []
        if feed_id:
            where.append("a.feed_id=?")
            args.append(feed_id)
        if folder_id:
            ids = self.descendants(folder_id)
            where.append("f.folder_id IN (" + ",".join("?" for _ in ids) + ")")
            args.extend(ids)
        if unread:
            where.append("a.read=0")
        if view == "starred":
            where.append("a.starred=1")
        if view == "history":
            where.append("EXISTS(SELECT 1 FROM events e WHERE e.article_id=a.id AND e.kind='open')")
        if tag:
            where.append("EXISTS(SELECT 1 FROM json_each(a.tags) WHERE value=?)")
            args.append(tag)
        if q.strip():
            # Literal tokens make punctuation and user input safe FTS queries.
            terms = regex.findall(r"[\p{L}\p{N}_]+", q)[:30]
            if terms:
                where.append("a.rowid IN (SELECT rowid FROM article_search WHERE article_search MATCH ?)")
                args.append(" AND ".join('"' + t + '"' for t in terms))
            else:
                where.append("0")
        return " AND ".join(where), args

    def articles(
        self,
        view="all",
        feed_id=None,
        folder_id=None,
        unread=False,
        q="",
        tag=None,
        sort="newest",
        offset=0,
        limit=100,
        deduplicate=True,
    ):
        where, args = self._where(
            view=view, feed_id=feed_id, folder_id=folder_id, unread=unread, q=q, tag=tag
        )
        if deduplicate and view != "starred":
            # A suppressed story must have an earlier surviving match. Evaluate
            # visibility at read time so delete, purge, undo and old backups all
            # work without rewriting user state or maintaining duplicate flags.
            where += """ AND (a.duplicate=0 OR NOT EXISTS(
                SELECT 1 FROM articles b JOIN feeds bf ON bf.id=b.feed_id
                WHERE b.deleted=0 AND bf.deleted=0 AND b.feed_id!=a.feed_id
                AND (b.added<a.added OR (b.added=a.added AND b.id<a.id))
                AND title_similarity(a.title,b.title)>=94))"""
        order = (
            "(SELECT max(e.created) FROM events e WHERE e.article_id=a.id AND e.kind='open') DESC"
            if view == "history"
            else "a.published " + ("ASC" if sort == "oldest" else "DESC")
        )
        total = self.one(
            f"SELECT count(*) n FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE {where}", args
        )["n"]
        items = self.rows(
            f"""SELECT a.id,a.feed_id,a.title,a.url,a.author,a.published,a.read,a.starred,
          a.read_at,a.words,a.tags,a.duplicate,substr(a.text,1,190) excerpt,f.title feed_title,f.folder_id
          FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE {where} ORDER BY {order},a.id LIMIT ? OFFSET ?""",
            [*args, max(1, min(int(limit), 300)), max(0, int(offset))],
        )
        return {"items": items, "total": total}

    def article(self, id):
        a = self.one(
            "SELECT a.*,f.title feed_title,f.url feed_url,f.options FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE a.id=?",
            (id,),
        )
        a["source_url"] = a["url"]
        a["url"] = a["url"] or a["feed_url"]
        a.pop("feed_url")
        a["body"] = sanitize(a["body"], a["url"])
        a["reading_minutes"] = max(1, math.ceil(a["words"] / 230))
        a["embed"] = embed_url(a["url"], self.settings()["invidious"])
        a["downloads"] = self.rows(
            "SELECT id,status,error,json_extract(payload,'$.url') url FROM jobs WHERE kind='download' AND json_extract(payload,'$.article_id')=? ORDER BY created DESC",
            (id,),
        )
        a.pop("options")
        return a

    def _undo(self, label, snapshots):
        id = uid()
        self.db.execute("INSERT INTO undo VALUES (?,?,?,?)", (id, label, json.dumps(snapshots), now()))
        return id

    def change_articles(self, ids, read=None, starred=None, deleted=None, tags=None, opened=False):
        if len(ids) > 10000:
            raise ValueError("Batch too large.")
        changes = {
            k: int(v) for k, v in dict(read=read, starred=starred, deleted=deleted).items() if v is not None
        }
        if tags is not None:
            changes["tags"] = json.dumps(tags)
        if read is True:
            changes["read_at"] = now()
        if read is not None:
            changes["state_changed"] = now()
        if not changes:
            return {"ok": True}
        with self.db:
            articles = [
                dict(self.db.execute("SELECT * FROM articles WHERE id=?", (id,)).fetchone() or {})
                for id in ids
            ]
            if any(not a for a in articles):
                raise ValueError("Article not found.")
            undo = self._undo(
                "Article changes", [("articles", a["id"], {k: a[k] for k in changes}) for a in articles]
            )
            for a in articles:
                self.db.execute(
                    "UPDATE articles SET " + ",".join(k + "=?" for k in changes) + " WHERE id=?",
                    [*changes.values(), a["id"]],
                )
                if opened or (read is not None and bool(read) != bool(a["read"])):
                    self.db.execute(
                        "INSERT INTO events(article_id,feed_id,kind,created) VALUES(?,?,?,?)",
                        (a["id"], a["feed_id"], "open" if opened else "read" if read else "unread", now()),
                    )
        return {"ok": True, "undo": undo}

    def mark_all(self, feed_id=None, folder_id=None):
        where, args = self._where(feed_id=feed_id, folder_id=folder_id, unread=True)
        ids = [
            a["id"]
            for a in self.rows(
                f"SELECT a.id FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE {where}", args
            )
        ]
        # One snapshot, one transaction, regardless of stream size.
        with self.db:
            undo = self._undo(
                "Mark stream as read",
                [
                    ("articles", a["id"], {"read": a["read"], "read_at": a["read_at"]})
                    for a in self.rows(
                        f"SELECT a.id,a.read,a.read_at FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE {where}",
                        args,
                    )
                ],
            )
            self.db.executemany(
                "UPDATE articles SET read=1,read_at=?,state_changed=? WHERE id=?",
                [(now(), now(), id) for id in ids],
            )
        return {"count": len(ids), "undo": undo}

    def undo(self, id=None):
        item = (
            self.one("SELECT * FROM undo WHERE id=?", (id,))
            if id
            else self.one("SELECT * FROM undo ORDER BY created DESC LIMIT 1")
        )
        with self.db:
            for table, key, values in json.loads(item["snapshot"]):
                if table not in ("feeds", "articles") or set(values) - {
                    "deleted",
                    "read",
                    "read_at",
                    "starred",
                    "tags",
                    "state_changed",
                }:
                    raise ValueError("Invalid undo snapshot.")
                if "read" in values:
                    values["state_changed"] = now()
                self.db.execute(
                    f"UPDATE {table} SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                    [*values.values(), key],
                )
            self.db.execute("DELETE FROM undo WHERE id=?", (item["id"],))
        return {"ok": True}

    def rules(self):
        return self.rows("SELECT * FROM rules ORDER BY rowid")

    def save_rule(self, name, field, pattern, action, value="", feed_id=None, enabled=True, id=None):
        if field not in ("title", "body", "url", "author", "tags") or action not in (
            "read",
            "star",
            "tag",
            "drop",
            "rewrite",
        ):
            raise ValueError("Invalid rule field or action.")
        regex.compile(pattern)
        if feed_id:
            self.one("SELECT id FROM feeds WHERE id=?", (feed_id,))
        id = id or uid()
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO rules VALUES (?,?,?,?,?,?,?,?)",
                (id, name, field, pattern, action, value, feed_id, int(enabled)),
            )
        return {"id": id}

    def delete_rule(self, id):
        with self.db:
            self.db.execute("DELETE FROM rules WHERE id=?", (id,))
        return {"ok": True}

    def apply_rules(self, a):
        for rule in self.rules():
            if not rule["enabled"] or rule["feed_id"] not in (None, a["feed_id"]):
                continue
            source = str(a.get(rule["field"], ""))
            if not regex.search(rule["pattern"], source, regex.I, timeout=0.05):
                continue
            if rule["action"] == "rewrite":
                a["body"] = regex.sub(rule["pattern"], rule["value"], a["body"], flags=regex.I, timeout=0.05)
            elif rule["action"] == "tag":
                a["tags"] = sorted(set(a["tags"] + [rule["value"]]))
            else:
                a[{"read": "read", "star": "starred", "drop": "deleted"}[rule["action"]]] = 1
        a["body"] = sanitize(a["body"], a["url"])
        a["text"] = plain(a["body"])
        a["words"] = len(a["text"].split()) + len(a.get("transcript", "").split())
        return a

    def ingest(
        self,
        feed_id,
        guid,
        title,
        url,
        body="",
        author="",
        published=None,
        enclosures=None,
        transcript="",
        tags=None,
    ):
        url = clean_url(url)
        body = sanitize(body, url)
        digest = fingerprint(title, plain(body))
        guid = guid or url or digest
        if self.rows("SELECT 1 FROM tombstones WHERE feed_id=? AND guid=?", (feed_id, guid)):
            return None
        existing = self.rows(
            "SELECT * FROM articles WHERE feed_id=? AND (guid=? OR (url=? AND url!='') OR fingerprint=?) LIMIT 1",
            (feed_id, guid, url, digest),
        )
        if existing:
            a = existing[0]
            if transcript and not a["transcript"]:
                with self.db:
                    self.db.execute(
                        "UPDATE articles SET transcript=?,words=words+? WHERE id=?",
                        (transcript, len(transcript.split()), a["id"]),
                    )
            if a["guid"] == guid and a["fingerprint"] != digest:
                a.update(title=title or a["title"], author=author or a["author"], fingerprint=digest)
                if not a["extracted"]:
                    a["body"] = body
                a = self.apply_rules(a)
                with self.db:
                    self.db.execute(
                        "UPDATE articles SET title=?,author=?,body=?,text=?,words=?,fingerprint=?,tags=?,read=?,starred=?,deleted=? WHERE id=?",
                        (
                            a["title"],
                            a["author"],
                            a["body"],
                            a["text"],
                            a["words"],
                            digest,
                            json.dumps(a["tags"]),
                            a["read"],
                            a["starred"],
                            a["deleted"],
                            a["id"],
                        ),
                    )
            return a["id"]
        a = dict(
            id=uid(),
            feed_id=feed_id,
            guid=guid,
            url=url,
            title=title or "(Untitled)",
            author=author or "",
            published=published or now(),
            added=now(),
            body=body,
            text=plain(body),
            transcript=transcript,
            enclosures=enclosures or [],
            tags=tags or [],
            read=0,
            starred=0,
            deleted=0,
            duplicate=0,
            read_at=None,
            words=0,
            fingerprint=digest,
            extracted=0,
            state_changed=None,
        )
        recent = self.rows(
            "SELECT title,feed_id FROM articles WHERE published>? AND deleted=0 ORDER BY published DESC LIMIT 1000",
            ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),),
        )
        if len(title) >= 20:
            a["duplicate"] = int(
                any(
                    x["feed_id"] != feed_id and ratio(title.casefold(), x["title"].casefold()) >= 94
                    for x in recent
                )
            )
        a = self.apply_rules(a)
        with self.db:
            self.db.execute(
                "INSERT INTO articles (" + ",".join(a) + ") VALUES (" + ",".join("?" for _ in a) + ")",
                [json.dumps(v) if k in JSON_FIELDS else v for k, v in a.items()],
            )
        return a["id"]

    def enqueue(self, kind, payload=None):
        if kind not in ("refresh", "extract", "download", "backfill", "purge", "rules"):
            raise ValueError("Unknown job type.")
        payload = payload or {}
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._enqueue(kind, payload)

    def _enqueue(self, kind, payload):
        encoded = json.dumps(payload, sort_keys=True)
        pending = self.rows(
            "SELECT id FROM jobs WHERE kind=? AND payload=? AND status IN ('queued','running')",
            (kind, encoded),
        )
        if pending:
            return pending[0]
        id = uid()
        self.db.execute(
            "INSERT INTO jobs(id,kind,payload,created,updated) VALUES(?,?,?,?,?)",
            (id, kind, encoded, now(), now()),
        )
        return {"id": id}

    def refresh_interval(self, feed_id, options):
        dates = [
            datetime.fromisoformat(a["published"])
            for a in self.rows(
                "SELECT published FROM articles WHERE feed_id=? ORDER BY published DESC LIMIT 25", (feed_id,)
            )
        ]
        gaps = [(a - b).total_seconds() / 60 for a, b in zip(dates, dates[1:]) if a > b]
        return int(
            options.get("interval") or (max(15, min(1440, statistics.median(gaps) / 2)) if gaps else 60)
        )

    def refresh(self, feed_id):
        f = self.one("SELECT * FROM feeds WHERE id=? AND deleted=0", (feed_id,))
        if not f["url"].startswith("http"):
            return {"new": 0}
        try:
            options = f["options"]
            if options.get("scrape_selector"):
                new = self.scrape(f)
            else:
                moved = []
                with self.reader(options, moved) as reader:
                    reader.add_feed(f["url"], exist_ok=True)
                    reader.update_feed(f["url"])
                    rf = reader.get_feed(f["url"])
                    new = 0
                    for entry in reader.get_entries(feed=f["url"]):
                        before = self.db.total_changes
                        transcript = ""
                        transcript_links = [
                            e
                            for e in entry.enclosures
                            if e.type
                            in (
                                "text/plain",
                                "text/vtt",
                                "application/srt",
                                "application/x-subrip",
                                "application/json",
                            )
                        ]
                        stored = self.rows(
                            "SELECT transcript FROM articles WHERE feed_id=? AND guid=?", (feed_id, entry.id)
                        )
                        if not stored or not stored[0]["transcript"]:
                            for link in transcript_links:
                                try:
                                    raw, _, _ = fetch(urljoin(f["url"], link.href), options)
                                    if link.type == "application/json":
                                        document = json.loads(raw)
                                        transcript = "\n".join(
                                            s.get("body", s.get("text", ""))
                                            for s in document.get("segments", [])
                                        )
                                    else:
                                        transcript = plain(raw.decode("utf-8", errors="replace"))
                                    break
                                except Exception:
                                    continue  # A broken optional transcript must not break the feed.
                        body = next(
                            (
                                c.value if c.type != "text/plain" else text_html(c.value)
                                for c in entry.content
                                if c.type in ("text/html", "text/xhtml", "text/plain")
                            ),
                            entry.summary or "",
                        )
                        id = self.ingest(
                            feed_id,
                            entry.id,
                            entry.title or "",
                            entry.link or "",
                            body,
                            entry.authors_str,
                            (entry.published or entry.updated or entry.added).isoformat(),
                            [dataclasses.asdict(e) for e in entry.enclosures],
                            transcript=transcript,
                        )
                        if self.db.total_changes > before:
                            new += 1
                            if options.get("extract") and id:
                                self.enqueue("extract", {"article_id": id})
                    with self.db:
                        self.db.execute(
                            "UPDATE feeds SET title=?,site_url=? WHERE id=?",
                            (
                                f["title"] if options.get("custom_title") else rf.title or f["title"],
                                rf.link or "",
                                feed_id,
                            ),
                        )
                    if moved and moved[-1] != f["url"]:
                        safe_url(moved[-1])
                        reader.change_feed_url(f["url"], moved[-1])
                        with self.db:
                            self.db.execute("UPDATE feeds SET url=? WHERE id=?", (moved[-1], feed_id))
            dates = [
                datetime.fromisoformat(a["published"])
                for a in self.rows(
                    "SELECT published FROM articles WHERE feed_id=? ORDER BY published DESC LIMIT 25",
                    (feed_id,),
                )
            ]
            interval = self.refresh_interval(feed_id, options)
            with self.db:
                self.db.execute(
                    "UPDATE feeds SET checked=?,next_check=?,last_article=?,interval=?,error=NULL,failures=0 WHERE id=?",
                    (
                        now(),
                        (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(),
                        dates[0].isoformat() if dates else None,
                        interval,
                        feed_id,
                    ),
                )
            return {"new": new}
        except Exception as error:
            with self.db:
                self.db.execute(
                    "UPDATE feeds SET checked=?,error=?,failures=failures+1,next_check=? WHERE id=?",
                    (
                        now(),
                        str(error)[:1000],
                        (
                            datetime.now(timezone.utc)
                            + timedelta(minutes=min(1440, 15 * 2 ** min(f["failures"], 7)))
                        ).isoformat(),
                        feed_id,
                    ),
                )
            raise

    def scrape(self, feed):
        data, url, _ = fetch(feed["url"], feed["options"])
        soup = BeautifulSoup(data, "html.parser")
        items = soup.select(feed["options"]["scrape_selector"])
        if not items:
            raise ValueError("Site scraper matched no items; check the CSS selector.")
        count = 0
        for item in items[:500]:
            link = item.select_one(feed["options"].get("scrape_link") or "a[href]")
            if not link and item.name == "a":
                link = item
            if link and link.get("href"):
                article_url = urljoin(url, link["href"])
                before = self.db.total_changes
                id = self.ingest(
                    feed["id"], article_url, link.get_text(" ", strip=True), article_url, str(item)
                )
                if self.db.total_changes > before:
                    count += 1
                    if feed["options"].get("extract"):
                        self.enqueue("extract", {"article_id": id})
        return count

    def extract_article(self, article_id):
        a = self.article(article_id)
        if not a["source_url"]:
            raise ValueError("This article has no source page URL to extract.")
        f = self.one("SELECT * FROM feeds WHERE id=?", (a["feed_id"],))
        result = extract(a["url"], f["options"])
        a.update(body=result["body"], url=result["url"], transcript=result["transcript"] or a["transcript"])
        if result["paywall"]:
            a["tags"] = sorted(set(a["tags"] + ["paywall"]))
        a = self.apply_rules(a)
        with self.db:
            self.db.execute(
                "UPDATE articles SET extracted=1,body=?,text=?,url=?,transcript=?,tags=?,words=?,read=?,starred=?,deleted=? WHERE id=?",
                (
                    a["body"],
                    a["text"],
                    a["url"],
                    a["transcript"],
                    json.dumps(a["tags"]),
                    a["words"],
                    a["read"],
                    a["starred"],
                    a["deleted"],
                    article_id,
                ),
            )
        return {"id": article_id}

    def download(self, article_id, url, job_id):
        a = self.article(article_id)
        if not any(e["href"] == url and (e.get("type") or "").startswith("audio/") for e in a["enclosures"]):
            raise ValueError("Only audio enclosures belonging to this article can be downloaded.")
        # Bounded, streamed download. Atomic rename exposes only complete episodes.
        safe_url(url)
        import httpx

        path = self.root / "downloads" / job_id
        partial = path.with_suffix(".part")
        try:
            with httpx.Client(timeout=60, follow_redirects=False) as client:
                for _ in range(10):
                    safe_url(url)
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers["location"])
                            continue
                        response.raise_for_status()
                        size = 0
                        with partial.open("wb") as output:
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > int(os.getenv("FEEDELIO_MAX_EPISODE_MB", "500")) * 1024 * 1024:
                                    raise ValueError("Episode exceeds download size limit.")
                                output.write(chunk)
                        partial.replace(path)
                        return {"bytes": size}
            raise ValueError("Too many redirects.")
        finally:
            partial.unlink(missing_ok=True)

    def download_path(self, id):
        self.one("SELECT id FROM jobs WHERE id=? AND kind='download' AND status='done'", (id,))
        if not regex.fullmatch("[a-f0-9]{32}", id):
            raise ValueError("Invalid download id.")
        return self.root / "downloads" / id

    def delete_download(self, id):
        path = self.download_path(id)
        path.unlink(missing_ok=True)
        with self.db:
            self.db.execute("UPDATE jobs SET status='removed' WHERE id=?", (id,))
        return {"ok": True}

    def backfill(self, feed_id, url=None, max_pages=10):
        f = self.one("SELECT * FROM feeds WHERE id=?", (feed_id,))
        url = url or f["options"].get("backfill_url") or f["url"]
        visited = set()
        pages = 0
        while url and url not in visited and pages < min(100, max(1, int(max_pages))):
            safe_url(url)
            visited.add(url)
            with self.reader(f["options"]) as reader:
                existed = reader.get_feed(url, None) is not None
                reader.add_feed(url, exist_ok=True)
                try:
                    reader.update_feed(url)
                    for e in reader.get_entries(feed=url):
                        body = next((c.value for c in e.content), e.summary or "")
                        self.ingest(
                            feed_id,
                            e.id,
                            e.title or "",
                            e.link or "",
                            body,
                            e.authors_str,
                            (e.published or e.updated or e.added).isoformat(),
                            [dataclasses.asdict(x) for x in e.enclosures],
                        )
                finally:
                    if not existed:
                        reader.delete_feed(url)
            data, final, _ = fetch(url, f["options"])
            try:
                next_url = json.loads(data).get("next_url")
            except (ValueError, AttributeError):
                soup = BeautifulSoup(data, "xml")
                link = soup.find("link", rel="next")
                next_url = link.get("href") if link else None
            url = urljoin(final, next_url) if next_url else None
            pages += 1
        return {"pages": pages, "next_url": url}

    def purge(self):
        count = 0
        for f in self.feeds():
            days = f["options"].get("retention_days", self.settings()["retention_days"])
            if f["options"].get("archive") or not days:
                continue
            cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
            doomed = self.rows(
                "SELECT id,guid FROM articles WHERE feed_id=? AND added<? AND starred=0 AND read=1",
                (f["id"], cutoff),
            )
            with self.db:
                self.db.executemany(
                    "INSERT OR IGNORE INTO tombstones VALUES (?,?)", [(f["id"], a["guid"]) for a in doomed]
                )
                self.db.executemany("DELETE FROM articles WHERE id=?", [(a["id"],) for a in doomed])
            with self.reader() as reader:
                # Public delete_entry only accepts manually added entries. This is
                # the same pinned internal primitive reader's retention plugins use.
                reader._storage.delete_entries([(f["url"], a["guid"]) for a in doomed])
            count += len(doomed)
        with self.db:
            self.db.execute(
                "DELETE FROM undo WHERE created<?",
                ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),),
            )
        return {"purged": count}

    def tick(self):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES('worker_heartbeat',?)", (json.dumps(now()),)
            )
        for f in self.rows(
            "SELECT id FROM feeds WHERE deleted=0 AND url LIKE 'http%' AND (next_check IS NULL OR next_check<=?)",
            (now(),),
        ):
            self.enqueue("refresh", {"feed_id": f["id"]})
        if not self.rows(
            "SELECT id FROM jobs WHERE kind='purge' AND created>?",
            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),),
        ):
            self.enqueue("purge")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            jobs = self.rows("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1")
            if not jobs:
                return False
            job = jobs[0]
            self.db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (now(), job["id"]))
        try:
            p = job["payload"]
            if job["kind"] == "refresh":
                result = self.refresh(**p) if p.get("feed_id") else self.refresh_all()
            elif job["kind"] == "extract":
                result = self.extract_article(**p)
            elif job["kind"] == "download":
                result = self.download(**p, job_id=job["id"])
            elif job["kind"] == "backfill":
                result = self.backfill(**p)
            elif job["kind"] == "rules":
                result = self.reapply_rules()
            else:
                result = self.purge()
            with self.db:
                self.db.execute(
                    "UPDATE jobs SET status='done',updated=?,result=? WHERE id=?",
                    (now(), json.dumps(result), job["id"]),
                )
        except Exception as error:
            with self.db:
                self.db.execute(
                    "UPDATE jobs SET status='failed',updated=?,error=? WHERE id=?",
                    (now(), str(error)[:1000], job["id"]),
                )
        return True

    def refresh_all(self):
        for f in self.feeds():
            self.enqueue("refresh", {"feed_id": f["id"]})
        return {"queued": True}

    def reapply_rules(self):
        count = 0
        for row in self.rows("SELECT * FROM articles WHERE deleted=0"):
            a = self.apply_rules(row)
            with self.db:
                self.db.execute(
                    "UPDATE articles SET body=?,text=?,words=?,tags=?,read=?,starred=?,deleted=? WHERE id=?",
                    (
                        a["body"],
                        a["text"],
                        a["words"],
                        json.dumps(a["tags"]),
                        a["read"],
                        a["starred"],
                        a["deleted"],
                        a["id"],
                    ),
                )
            count += 1
        return {"processed": count}

    def import_opml(self, xml):
        root = fromstring(xml)
        if root.tag.lower() != "opml":
            raise ValueError("Expected an OPML document.")
        count = 0

        # Validate every URL before opening the write transaction (DNS can be
        # slow). Folder/name/depth errors below still roll back the whole import.
        for child in root.iter():
            for key, value in child.attrib.items():
                if key.lower() == "xmlurl":
                    safe_url(value)

        def walk(node, folder="inbox", depth=0):
            nonlocal count
            if depth > 50:
                raise ValueError("Folder nesting is too deep.")
            for child in node:
                attrs = {k.lower(): v for k, v in child.attrib.items()}
                name = attrs.get("text") or attrs.get("title") or "Untitled"
                target = folder
                if child.tag.lower() == "outline":
                    if attrs.get("xmlurl"):
                        self._subscribe(attrs["xmlurl"], folder, name, {})
                        count += 1
                    else:
                        parent = None if folder == "inbox" else folder
                        matches = self.rows(
                            "SELECT id FROM folders WHERE name=? AND parent_id IS ?", (name, parent)
                        )
                        if not name.strip():
                            raise ValueError("A folder needs a name.")
                        target = matches[0]["id"] if matches else uid()
                        if not matches:
                            self.db.execute(
                                "INSERT INTO folders VALUES (?,?,?)", (target, name.strip(), parent)
                            )
                walk(child, target, depth + 1)

        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            walk(root)
        return {"imported": count}

    def export_opml(self):
        root = ET.Element("opml", version="2.0")
        ET.SubElement(ET.SubElement(root, "head"), "title").text = "Feedelio subscriptions"
        body = ET.SubElement(root, "body")
        folders, feeds = self.folders(), self.feeds()

        def walk(parent, node):
            for folder in (f for f in folders if f["parent_id"] == parent):
                sub = node if folder["id"] == "inbox" else ET.SubElement(node, "outline", text=folder["name"])
                for feed in (
                    f for f in feeds if f["folder_id"] == folder["id"] and f["url"].startswith("http")
                ):
                    ET.SubElement(
                        sub,
                        "outline",
                        type="rss",
                        text=feed["title"],
                        title=feed["title"],
                        xmlUrl=feed["url"],
                        htmlUrl=feed["site_url"],
                    )
                walk(folder["id"], sub)

        walk(None, body)
        ET.indent(root)
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def backup(self):
        tables = [
            "folders",
            "feeds",
            "articles",
            "rules",
            "settings",
            "events",
            "tombstones",
            "chrome_state",
            "jobs",
            "undo",
        ]
        with self.db:
            self.db.execute("BEGIN")
            data = {t: [dict(r) for r in self.db.execute(f"SELECT * FROM {t}")] for t in tables}
            media = {
                j["id"]: base64.b64encode(self.download_path(j["id"]).read_bytes()).decode("ascii")
                for j in data["jobs"]
                if j["kind"] == "download" and j["status"] == "done"
            }
        return {"format": "feedelio", "version": 1, "created": now(), "tables": data, "media": media}

    def restore(self, backup):
        if backup.get("format") != "feedelio" or backup.get("version") != 1:
            raise ValueError("Unsupported backup format.")
        if self.rows("SELECT id FROM feeds LIMIT 1"):
            raise ValueError(
                "Restore requires an empty library. Use a new data directory to preserve the current library."
            )
        tables = [
            "folders",
            "feeds",
            "articles",
            "rules",
            "settings",
            "events",
            "tombstones",
            "chrome_state",
            "jobs",
            "undo",
        ]
        data = backup.get("tables", {})
        if set(data) != set(tables):
            raise ValueError("Backup is missing required tables.")
        # Validate tree and media before entering the restore transaction.
        folders = {f["id"]: f["parent_id"] for f in data["folders"]}
        for id in folders:
            seen = set()
            while id is not None:
                if id in seen or id not in folders:
                    raise ValueError("Invalid folder tree in backup.")
                seen.add(id)
                id = folders[id]
        media = backup.get("media", {})
        expected_media = {j["id"] for j in data["jobs"] if j["kind"] == "download" and j["status"] == "done"}
        if set(media) != expected_media or any(not regex.fullmatch("[a-f0-9]{32}", id) for id in media):
            raise ValueError("Backup has missing or invalid downloaded episodes.")
        decoded = {id: base64.b64decode(value, validate=True) for id, value in media.items()}
        if any((self.root / "downloads" / id).exists() for id in decoded):
            raise ValueError("Restore requires an empty downloads directory.")
        with self.db:
            self.db.execute("PRAGMA defer_foreign_keys=ON")
            for table in tables:
                valid = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                for row in data[table]:
                    if set(row) != valid:
                        raise ValueError(f"Invalid {table} row.")
                    self.db.execute(
                        f"INSERT OR REPLACE INTO {table} ("
                        + ",".join(row)
                        + ") VALUES ("
                        + ",".join("?" for _ in row)
                        + ")",
                        list(row.values()),
                    )
            self.db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            # Files are new, validated IDs. A failed write rolls back the database.
            written = []
            try:
                for id, content in decoded.items():
                    path = self.root / "downloads" / id
                    written.append(path)
                    path.write_bytes(content)
                if self.db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Backup contains invalid references.")
            except Exception:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
        return {"restored": len(data["articles"])}

    def obsidian(self, id):
        a = self.article(id)
        front = "\n".join(
            [
                f"title: {json.dumps(a['title'])}",
                f"source: {json.dumps(a['url'])}",
                f"published: {json.dumps(a['published'])}",
                f"tags: {json.dumps(a['tags'])}",
            ]
        )
        return f"---\n{front}\n---\n\n# {a['title']}\n\n{markdownify(a['body'])}\n\n" + (
            f"## Transcript\n\n{a['transcript']}" if a["transcript"] else ""
        )

    def statistics(self):
        return dict(
            totals=self.one(
                "SELECT count(*) articles,coalesce(sum(read),0) read,coalesce(sum(starred),0) saved,coalesce(sum(CASE WHEN read=1 THEN words ELSE 0 END),0) words FROM articles WHERE deleted=0"
            ),
            days=self.rows(
                "SELECT substr(created,1,10) day,count(*) opens FROM events WHERE kind='open' GROUP BY day ORDER BY day DESC LIMIT 30"
            ),
            feeds=self.rows("""SELECT f.title,count(a.id) total,coalesce(sum(a.read),0) read,
              coalesce(sum(a.starred),0) saved FROM feeds f LEFT JOIN articles a ON a.feed_id=f.id AND a.deleted=0
              WHERE f.deleted=0 GROUP BY f.id ORDER BY read DESC"""),
        )

    def chrome_sync(self, entries):
        feed_id = "chrome"
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO feeds(id,url,title,folder_id,created) VALUES('chrome','chrome:reading-list','Chrome Reading List','inbox',?)",
                (now(),),
            )
        result = []
        for e in entries:
            url = e["url"]
            if urlsplit(url).scheme not in ("http", "https"):
                continue
            # Preserve exact Chrome URLs for synchronization; cleaning only affects the article URL.
            id = self.ingest(feed_id, url, e.get("title") or url, url)
            if not id:
                continue
            a = self.article(id)
            previous = self.rows("SELECT chrome_updated FROM chrome_state WHERE url=?", (url,))
            changed = not previous or e["lastUpdateTime"] > previous[0]["chrome_updated"]
            local_time = (
                datetime.fromisoformat(a["state_changed"]).timestamp() * 1000 if a["state_changed"] else 0
            )
            if changed and e["lastUpdateTime"] > local_time:
                with self.db:
                    stamp = datetime.fromtimestamp(e["lastUpdateTime"] / 1000, timezone.utc).isoformat()
                    self.db.execute(
                        "UPDATE articles SET read=?,read_at=?,state_changed=? WHERE id=?",
                        (int(e["hasBeenRead"]), stamp if e["hasBeenRead"] else a["read_at"], stamp, id),
                    )
                a["read"] = e["hasBeenRead"]
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO chrome_state VALUES (?,?)", (url, e["lastUpdateTime"])
                )
            if not a["body"] and not self.rows(
                "SELECT id FROM jobs WHERE kind='extract' AND json_extract(payload,'$.article_id')=?", (id,)
            ):
                self.enqueue("extract", {"article_id": id})
            result.append({"url": url, "hasBeenRead": bool(a["read"])})
        return {"entries": result}

    def execute(self, action, payload):
        actions = {
            "subscribe",
            "discover",
            "save_folder",
            "delete_folder",
            "edit_feed",
            "bulk_feeds",
            "change_articles",
            "mark_all",
            "undo",
            "save_settings",
            "save_rule",
            "delete_rule",
            "enqueue",
            "import_opml",
            "restore",
            "chrome_sync",
            "delete_download",
        }
        if action not in actions:
            raise ValueError("Unknown action.")
        return getattr(self, action)(**payload)
