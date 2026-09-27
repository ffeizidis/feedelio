"""Application boundary. API, MCP and worker depend on this package only.

reader owns feed transport/parsing/raw-entry persistence. The application DB is a
transactional projection containing processed content and user-owned state.
"""

from __future__ import annotations

import base64
import inspect
import io
import json
import math
import os
import random
import socket
import sqlite3
import statistics
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import regex
import requests
from bs4 import BeautifulSoup
from defusedxml.ElementTree import fromstring
from markdownify import markdownify
from rapidfuzz.fuzz import ratio
from reader import make_reader
from reader._parser.jsonfeed import JSONFeedParser
from reader._storage import Storage
from reader.discover import from_http_response

from . import backfill as archives
from .content import (
    UnresolvedHost,
    bounded,
    clean_url,
    embed_url,
    extract,
    fetch,
    fingerprint,
    is_youtube,
    local_frame_origin,
    looks_like_html,
    origin,
    plain,
    probe_video,
    request_headers,
    request_options,
    safe_url,
    same_feed,
    sanitize,
    text_html,
    video_id,
)
from .network import PinnedAdapter, http_client
from .reader_extensions import BoundedParser, ExtendedFeedparser, entry_fields


def now(**delta):
    """The current UTC time as ISO text, optionally shifted (now(days=-30))."""
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def uid():
    return uuid.uuid4().hex


DEFAULTS = dict(
    theme="dark",
    font="serif",
    font_size=17,
    line_height=1.7,
    width=65,
    images=False,
    hide_read=False,
    sort="newest",
    deduplicate=True,
    retention_days=0,
    invidious="",
    rsshub="https://rsshub.app",
    sidebar_width=252,
    sidebar_unread_only=False,
    list_width=590,
    list_display="title",
    accent="blue",
    feed_colours=True,
    unread_marker="count",
    wordmark="script",
    reduce_motion=False,
    open_titles_in="reader",
    new_feed_read=True,
    extract_articles=True,
    collapsed_folders=[],
)
JSON_FIELDS = {"options", "tags", "enclosures", "payload", "result"}
# Articles are always read through their feed, so deleted feeds hide their articles.
ARTICLES = "FROM articles a JOIN feeds f ON f.id=a.feed_id"
# An enclosure without a type is still audio when its file says so.
AUDIO_LINK = " OR ".join(
    f"lower(json_extract(e.value,'$.href')) LIKE '%.{ext}%'"
    for ext in ("mp3", "m4a", "aac", "ogg", "opus", "wav")
)
# Chrome's Reading List lives in one hidden feed, shown only in its own view.
READING_LIST = "chrome"


def site_name(url):
    """A reading-list article's source: the website it is on."""
    host = urlsplit(url or "").hostname or ""
    return host.removeprefix("www.") or "Reading list"


# What a rule may look at, what it may do, and the article column each action sets.
RULE_FIELDS = ("title", "body", "url", "author", "tags")
RULE_ACTIONS = ("read", "star", "tag", "drop", "rewrite")
RULE_STATE = {"read": "read", "star": "starred", "drop": "deleted"}
# A pattern that has not matched within this long is pathological, not slow.
RULE_TIMEOUT = 0.05
# A feed with nothing new for this long is reported as stale, not healthy.
STALE_DAYS = 14
# Every feed is checked at least this often, so a new post shows within the
# hour; a feed that posts often is checked more often, down to the floor.
MAX_INTERVAL = 60
MIN_INTERVAL = 15
# Posting rhythm is judged on this recent window only: backfilled archive
# posts months apart would otherwise make every feed look quiet.
RHYTHM_DAYS = 30
# Refreshes run side by side, but never more than this many at one site.
PER_HOST = 2
# "Update now" for the whole library ranks ahead of scheduled refreshes but
# behind a feed asked for by name (manual=true, which ranks as 1).
EVERY_FEED = 0.5


def aware(value):
    """A stored ISO date as an aware datetime (UTC when it carries no zone)."""
    d = datetime.fromisoformat(value)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def not_future(published):
    """A publish date later than now is a publisher's mistake or a schedule;
    it would pin the article to the top of every list, so it reads as now."""
    try:
        return now() if published and aware(published) > datetime.now(timezone.utc) else published
    except ValueError:
        return published


def chain(error):
    """An exception and every exception it was raised from."""
    seen = []
    while error is not None and error not in seen:
        seen.append(error)
        error = error.__cause__ or error.__context__
    return seen


def unreachable(error):
    """Whether a fetch failed before reaching the publisher at all."""
    return any(
        isinstance(e, (UnresolvedHost, requests.exceptions.ConnectionError, socket.gaierror))
        for e in chain(error)
    )


# The worker refreshes feeds on several threads, and reader's database cannot
# take concurrent writers (a read upgraded to a write fails at once, whatever
# the timeout). Its calls are short; the network fetches between them are not
# held up by this lock and still run side by side.
READER_LOCK = threading.RLock()


class LockedStorage(Storage):
    """reader's storage with every method call taken one thread at a time."""

    def __getattribute__(self, name):
        attr = super().__getattribute__(name)
        if name.startswith("__") or not inspect.ismethod(attr):
            return attr

        def locked(*args, **kwargs):
            with READER_LOCK:
                return attr(*args, **kwargs)

        return locked


class HostSlots:
    """How many refreshes are running at each site, shared by the worker's threads."""

    def __init__(self, limit=PER_HOST):
        self.limit, self.busy, self.lock = limit, {}, threading.Lock()

    def take(self, host):
        with self.lock:
            if self.busy.get(host, 0) >= self.limit:
                return False
            self.busy[host] = self.busy.get(host, 0) + 1
            return True

    def release(self, host):
        with self.lock:
            self.busy[host] -= 1


def is_uid(id):
    return bool(regex.fullmatch("[a-f0-9]{32}", id or ""))


# Every table a backup carries; backup and restore must agree exactly.
BACKUP_TABLES = (
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
)


def record(row):
    result = dict(row)
    for key in JSON_FIELDS & result.keys():
        if result[key] is not None:
            result[key] = json.loads(result[key])
    return result


def rule_lines(pattern):
    """A rule's patterns, one per line, so one rule can list its alternatives."""
    return [line for line in (raw.strip() for raw in (pattern or "").splitlines()) if line]


def compile_rule(pattern, is_regex=True):
    """Compile every line of a rule once, naming the line that would not compile."""
    lines = rule_lines(pattern)
    if not lines:
        raise ValueError("A rule needs at least one pattern.")
    compiled = []
    for number, line in enumerate(lines, 1):
        try:
            compiled.append(regex.compile(line if is_regex else regex.escape(line), regex.I))
        except regex.error as error:
            raise ValueError(f"Line {number}: {error}") from error
    return compiled


def replacement_error(compiled, value):
    """Report a rewrite template referring to a group its pattern cannot capture."""
    for number, name in regex.findall(r"\\(\d+)|\\g<([^>]*)>", value or ""):
        if number and int(number) > compiled.groups:
            return f"There is no group {number} to substitute."
        if name and name not in compiled.groupindex:
            return f"There is no group named {name!r} to substitute."
    return None


def parse_feed(url, data, headers):
    """Parse fetched bytes with the retrieval parsers; raises when they are not a feed."""
    parser = JSONFeedParser() if data.lstrip().startswith(b"{") else ExtendedFeedparser()
    feed, entries = parser(url, io.BytesIO(data), headers)
    list(entries)
    return feed


def scraped_date(item):
    """A listing's own date for an item (a <time>, a data-date or an ISO date in the text), else None."""
    stamp = item.find("time", attrs={"datetime": True})
    candidates = [stamp["datetime"] if stamp else "", item.get("data-date") or ""]
    candidates += regex.findall(r"\b\d{4}-\d{2}-\d{2}\b", item.get_text(" ", strip=True))
    for value in candidates:
        try:
            when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).isoformat()
    return None


def feed_guesses(page_url, page):
    """Same-site feed URLs worth one request each: page links, robots.txt, then convention."""
    host = urlsplit(page_url).hostname
    hint = regex.compile(r"rss|atom|feed|\.xml$", regex.I)
    hrefs = [a["href"] for a in BeautifulSoup(page, "html.parser").select("a[href]")]
    try:
        robots = fetch(urljoin(page_url, "/robots.txt"), max_bytes=100_000)[0].decode(errors="replace")
        hrefs += [line.split(":", 1)[1] for line in robots.splitlines() if line.lower().startswith("allow:")]
    except Exception:
        pass
    hrefs += ["/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml", "/feeds/posts/default"]
    guesses = []
    for href in hrefs:
        url = urljoin(page_url, href.strip())
        parts = urlsplit(url)
        if parts.hostname == host and "sitemap" not in parts.path and hint.search(parts.path):
            guesses.append(url)
    return list(dict.fromkeys(guesses))[:12]


def retry_at(exc, attempts):
    """When to retry after a publisher pushed back: exponential, or its Retry-After."""
    from email.utils import parsedate_to_datetime

    delay = min(3600, 60 * 2 ** min(attempts, 6))
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("Retry-After", "") if response is not None else ""
    try:
        delay = max(delay, int(retry_after))
    except ValueError:
        try:
            delay = max(
                delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
            )
        except (ValueError, TypeError, OverflowError):
            pass
    # A publisher's Retry-After is honoured up to a day; beyond that it parks a feed for good.
    return datetime.now(timezone.utc) + timedelta(seconds=min(delay, 86400))


def configure_session(session, options):
    """Feedelio's identity and safety rails on a requests session: no ambient
    proxies or credentials, pinned hosts, one User-Agent for the whole app."""
    session.trust_env = False
    for scheme in ("http://", "https://"):
        session.mount(scheme, GuardAdapter(options.get("cookie_origin")))
    session.headers.update(request_headers(options))
    if options.get("proxy"):
        session.proxies = dict(http=options["proxy"], https=options["proxy"])
    return session


class GuardAdapter(PinnedAdapter):
    def __init__(self, cookie_origin=None):
        self.cookie_origin = cookie_origin
        super().__init__()

    def send(self, request, **kwargs):
        safe_url(request.url)  # Requests also calls this adapter for every redirect.
        if self.cookie_origin and origin(request.url) != origin(self.cookie_origin):
            request.headers.pop("Cookie", None)
        if not kwargs.get("timeout"):
            kwargs["timeout"] = (5, 30)
        return super().send(request, **kwargs)


class Core:
    # Schema and one-off migrations run once per process per data directory, so
    # opening a Core is a plain connect and never waits on a concurrent writer.
    initialised = set()
    # CSP frame sources per data directory; cleared by save_settings and restore.
    # Only the API process changes settings, so a second API process would need
    # its own invalidation.
    frame_cache = {}

    @staticmethod
    def data_root(data_dir=None):
        return Path(data_dir or os.getenv("FEEDELIO_DATA", "data")).resolve()

    def __init__(self, data_dir=None):
        self.root = Core.data_root(data_dir)
        self.db = (
            sqlite3.connect(self.root / "app.sqlite", timeout=30) if self.root in Core.initialised else None
        )
        if self.db is None:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "downloads").mkdir(exist_ok=True)
            self.db = sqlite3.connect(self.root / "app.sqlite", timeout=30)
            self.db.executescript(Path(__file__).with_name("schema.sql").read_text())
            if "duration" not in {r[1] for r in self.db.execute("PRAGMA table_info(articles)")}:
                try:  # The API and the worker start together; whoever is second finds it done.
                    self.db.execute("ALTER TABLE articles ADD COLUMN duration INTEGER NOT NULL DEFAULT 0")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise
            # Rules predating the literal/regex choice were regular expressions.
            if "is_regex" not in {r[1] for r in self.db.execute("PRAGMA table_info(rules)")}:
                try:
                    self.db.execute("ALTER TABLE rules ADD COLUMN is_regex INTEGER NOT NULL DEFAULT 1")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise
            # Stable subscription colours, including deleted feeds so restoring or
            # removing subscriptions never shifts the remaining palette.
            self.db.execute("""UPDATE feeds SET options=json_set(options,'$.hue',
              (SELECT count(*) FROM feeds older WHERE older.created<feeds.created
                OR (older.created=feeds.created AND older.id<feeds.id)) % 14)
              WHERE json_extract(options,'$.hue') IS NULL""")
            # Intervals once ran up to a day; bring every automatic one under the hour.
            self.db.execute(
                "UPDATE feeds SET interval=?,next_check=min(coalesce(next_check,?),?)"
                " WHERE interval>? AND json_extract(options,'$.interval') IS NULL",
                (MAX_INTERVAL, now(), now(minutes=MAX_INTERVAL), MAX_INTERVAL),
            )
            # Articles dated in the future stayed pinned to the top of the list.
            self.db.execute("UPDATE articles SET published=added WHERE published>added")
            self.db.commit()
            Core.initialised.add(self.root)
        self.db.row_factory = sqlite3.Row
        self.db.create_function(
            "title_similarity", 2, lambda a, b: ratio(a.casefold(), b.casefold()), deterministic=True
        )
        self.db.execute("PRAGMA foreign_keys=ON")

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
    def reader(self, options=None, moved=None, durations=None):
        options = options or {}

        def plugin(reader):
            @reader._parser.lazy_init
            def configure(parser):
                http = parser.get_retriever("http://")
                for mime, parsers in parser.parsers_by_mime_type.items():
                    parser.parsers_by_mime_type[mime] = [(q, BoundedParser(p, durations)) for q, p in parsers]
                configure_session(http.session, options)

                def response_hook(session, response, request, **kwargs):
                    if (
                        moved is not None
                        and response.history
                        and all(r.status_code in (301, 308) for r in response.history)
                    ):
                        moved.append(response.url)

                http.response_hooks.append(response_hook)

        path = str(self.root / "reader.sqlite")
        with READER_LOCK:  # Opening it writes too (it turns search off).
            reader = make_reader(
                path, plugins=[plugin], search_enabled=False, _storage=LockedStorage(path, timeout=30)
            )
        with reader:
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
        if merged["list_display"] not in ("title", "preview", "compact"):
            raise ValueError("Invalid article list display.")
        if merged["theme"] not in ("system", "light", "dark") or merged["font"] not in (
            "serif",
            "sans",
            "mono",
            "newsreader",
        ):
            raise ValueError("Invalid theme or font.")
        for key, lo, hi in [
            ("font_size", 16, 32),
            ("line_height", 1.4, 2.2),
            ("width", 45, 75),
            ("retention_days", 0, 36500),
            ("sidebar_width", 180, 600),
            ("list_width", 300, 1000),
        ]:
            if not isinstance(merged[key], (int, float)) or not lo <= merged[key] <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}.")
        for key, choices in {
            "accent": ("blue", "violet", "green", "amber", "red"),
            "unread_marker": ("count", "dot", "none"),
            "wordmark": ("script", "plain"),
            "open_titles_in": ("reader", "original"),
        }.items():
            if merged[key] not in choices:
                raise ValueError(f"Invalid {key}.")
        for key in (
            "feed_colours",
            "reduce_motion",
            "new_feed_read",
            "extract_articles",
            "sidebar_unread_only",
        ):
            if not isinstance(merged[key], bool):
                raise ValueError(f"{key} must be a boolean.")
        if merged["invidious"]:
            safe_url(merged["invidious"])
            local_frame_origin(merged["invidious"])
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO settings VALUES (?,?)",
                [(k, json.dumps(v)) for k, v in values.items()],
            )
        Core.frame_cache.pop(self.root, None)
        return self.settings()

    def frame_sources(self):
        if self.root not in Core.frame_cache:
            try:
                origin = local_frame_origin(self.settings()["invidious"])
            except ValueError:
                origin = ""
            Core.frame_cache[self.root] = "https:" + (" " + origin if origin else "")
        return Core.frame_cache[self.root]

    def folders(self):
        return self.rows("SELECT * FROM folders ORDER BY name COLLATE NOCASE")

    def descendants(self, folder_id):
        return [
            r["id"]
            for r in self.rows(
                """WITH RECURSIVE tree(id) AS (
          SELECT id FROM folders WHERE id=? UNION SELECT f.id FROM folders f JOIN tree t ON f.parent_id=t.id)
          SELECT id FROM tree""",
                (folder_id,),
            )
        ]

    def save_folder(self, name, parent_id=None, id=None):
        if not name.strip():
            raise ValueError("A folder needs a name.")
        if id == "inbox":
            raise ValueError("Unfiled is the permanent default folder.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if parent_id:
                self.one("SELECT id FROM folders WHERE id=?", (parent_id,))
            if id and parent_id in self.descendants(id):
                raise ValueError("A folder cannot contain itself or its ancestors.")
            id = id or uid()
            if self.rows(
                "SELECT 1 FROM folders WHERE name=? COLLATE NOCASE AND id!=? AND parent_id IS ?",
                (name.strip(), id, parent_id or None),
            ):
                raise ValueError(f"A folder named {name.strip()} already exists here.")
            self.db.execute(
                "INSERT INTO folders VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,parent_id=excluded.parent_id",
                (id, name.strip(), parent_id),
            )
        return {"id": id}

    def delete_folder(self, id):
        if id == "inbox":
            raise ValueError("Unfiled cannot be deleted.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            folder = self.one("SELECT * FROM folders WHERE id=?", (id,))
            clash = self.rows(
                """SELECT c.name FROM folders c JOIN folders s ON s.name=c.name COLLATE NOCASE
                  AND s.parent_id IS ? AND s.id!=? WHERE c.parent_id=?""",
                (folder["parent_id"], id, id),
            )
            if clash:
                raise ValueError(
                    f"Move or rename the subfolder {clash[0]['name']} first: its parent has one already."
                )
            self.db.execute("UPDATE feeds SET folder_id=? WHERE folder_id=?", ("inbox", id))
            self.db.execute("UPDATE folders SET parent_id=? WHERE parent_id=?", (folder["parent_id"], id))
            self.db.execute("DELETE FROM folders WHERE id=?", (id,))
            self._setting("collapsed_folders", [c for c in self.settings()["collapsed_folders"] if c != id])
        return {"ok": True}

    def feeds(self):
        # The reading list is not a subscription: it has its own view instead.
        feeds = self.rows(
            """SELECT f.*,count(CASE WHEN a.read=0 AND a.deleted=0 THEN 1 END) unread,
          count(CASE WHEN a.deleted=0 THEN 1 END) total,
          count(CASE WHEN a.deleted=0 AND (EXISTS(SELECT 1 FROM json_each(a.tags) WHERE value IN ('paywall','archive-preview'))) THEN 1 END) partial_items FROM feeds f
          LEFT JOIN articles a ON a.feed_id=f.id WHERE f.deleted=0 AND f.id!=?
          GROUP BY f.id ORDER BY f.title COLLATE NOCASE""",
            (READING_LIST,),
        )
        # One classification for the whole app: the library filter, the sidebar
        # dot and the feed list all read this instead of judging a feed again.
        stale = now(days=-STALE_DAYS)
        for f in feeds:
            f["health"] = (
                "error"
                if f["error"]
                else "stale"
                if (f["last_article"] or f["created"]) < stale
                else "partial"
                if f["partial_items"]
                else "healthy"
                if f["checked"]
                else "pending"
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
        counts = self.one(
            f"""SELECT coalesce(sum(f.id!=?),0) total,coalesce(sum(a.read=0 AND f.id!=?),0) unread,
          coalesce(sum(a.starred=1),0) starred,
          coalesce(sum(a.read=0 AND json_extract(f.options,'$.bookmarked')=1),0) bookmarks,
          coalesce(sum(a.read=0 AND f.id=?),0) reading
          {ARTICLES} WHERE a.deleted=0 AND f.deleted=0""",
            (READING_LIST,) * 3,
        )
        return dict(
            feeds=feeds,
            folders=folders,
            counts=counts,
            settings=self.settings(),
            jobs=self.rows("SELECT * FROM jobs ORDER BY created DESC LIMIT 30"),
            backfills=self.rows("SELECT * FROM jobs WHERE kind='backfill' ORDER BY created DESC"),
            downloads=self.rows("SELECT * FROM jobs WHERE kind='download' ORDER BY created DESC"),
            queue=self.queue(),
            undo=self.rows("SELECT id,label FROM undo ORDER BY created DESC LIMIT 1"),
            worker=self.rows("SELECT value FROM settings WHERE key='worker_heartbeat'"),
        )

    def queue(self):
        """The worker's backlog by kind: what is waiting, what ran in the last day, and
        how many older articles the archives have brought in so far."""
        day = now(days=-1)
        rows = self.rows(
            """SELECT kind,
              coalesce(sum(status IN ('queued','running')),0) waiting,
              coalesce(sum(status='done' AND updated>?),0) done,
              coalesce(sum(status='failed' AND updated>?),0) failed,
              coalesce(sum(CASE WHEN kind='backfill' THEN json_extract(result,'$.articles') END),0) articles
              FROM jobs GROUP BY kind""",
            (day, day),
        )
        return {r["kind"]: r for r in rows}

    def discover(self, url):
        url = safe_url(url)
        data, final, headers = fetch(url)
        if is_youtube(final):
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
        try:
            return [{"url": final, "title": parse_feed(final, data, headers).title or final}]
        except Exception:
            pass
        # Sites that advertise no feed often still serve one at a conventional
        # path, link it from the page body, or allow-list it in robots.txt.
        for candidate in feed_guesses(final, data):
            try:
                body, url, head = fetch(candidate)
                feed = parse_feed(url, body, head)
            except Exception:
                continue
            page_title = BeautifulSoup(data, "html.parser").title
            title = feed.title or (page_title.get_text(strip=True) if page_title else "") or url
            return [{"url": url, "title": title}]
        raise ValueError("No feed found. Try an explicit feed URL or configure a site scraper.")

    def subscribe(self, url, folder_id="inbox", title="", options=None):
        url = safe_url(url)
        options = self.validate_options(options or {})
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = [
                f
                for f in self.rows("SELECT url,title FROM feeds WHERE deleted=0")
                if same_feed(f["url"], url)
            ]
            if existing:
                raise ValueError(f"Already subscribed to this feed as {existing[0]['title']}.")
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
                json.dumps(
                    options
                    | {
                        "custom_title": bool(title.strip()),
                        "hue": self.one("SELECT count(*) n FROM feeds")["n"] % 14,
                    }
                ),
                now(),
            ),
        )
        self._enqueue("refresh", {"feed_id": id})
        return {"id": id}

    def validate_options(self, options):
        allowed = {
            "custom_title",
            "hue",
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
            "bookmarked",
            "mark_read",
        }
        if set(options) - allowed:
            raise ValueError("Unknown feed option.")
        if "hue" in options and (type(options["hue"]) is not int or not 0 <= options["hue"] < 14):
            raise ValueError("Invalid feed hue.")
        if options.get("interval") is not None and not 5 <= int(options["interval"]) <= 10080:
            raise ValueError("Refresh interval must be 5–10080 minutes, or null for automatic.")
        days = options.get("retention_days")
        if days is not None and (
            isinstance(days, bool) or not isinstance(days, (int, float)) or not 0 <= days <= 36500
        ):
            raise ValueError("Feed retention must be 0–36500 days, or null for the global setting.")
        for key in ("archive", "extract", "bookmarked", "mark_read"):
            if key in options and not isinstance(options[key], bool):
                raise ValueError(f"{key} must be a boolean.")
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
                    (interval, now(minutes=interval), id),
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
                self.enqueue("refresh", {"feed_id": f["id"], "manual": True})
        return {"ok": True, "undo": undo}

    def _where(
        self,
        view="all",
        feed_id=None,
        folder_id=None,
        unread=False,
        q="",
        tag=None,
        titles_only=False,
        **ignored,
    ):
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
        if view == "bookmarks":
            where.append("json_extract(f.options,'$.bookmarked')=1")
        # Reading-list articles show in their own view, and in Saved and History.
        if view == "reading":
            where.append("a.feed_id=?")
            args.append(READING_LIST)
        elif view in ("all", "bookmarks"):
            where.append("a.feed_id!=?")
            args.append(READING_LIST)
        if tag:
            where.append("EXISTS(SELECT 1 FROM json_each(a.tags) WHERE value=?)")
            args.append(tag)
        if q.strip():
            # Literal tokens make punctuation and user input safe FTS queries;
            # each one matches as a prefix, so a query finds words as they are typed.
            terms = regex.findall(r"[\p{L}\p{N}_]+", q)[:30]
            if terms:
                where.append("a.rowid IN (SELECT rowid FROM article_search WHERE article_search MATCH ?)")
                match = " AND ".join('"' + t + '"*' for t in terms)
                args.append("title : (" + match + ")" if titles_only else match)
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
        titles_only=False,
    ):
        where, args = self._where(
            view=view,
            feed_id=feed_id,
            folder_id=folder_id,
            unread=unread,
            q=q,
            tag=tag,
            titles_only=titles_only,
        )
        if deduplicate and view not in ("starred", "reading"):
            # A suppressed story must have an earlier surviving match. Evaluate
            # visibility at read time so delete, purge, undo and old backups all
            # work without rewriting user state or maintaining duplicate flags.
            where += """ AND (a.duplicate=0 OR NOT EXISTS(
                SELECT 1 FROM articles b JOIN feeds bf ON bf.id=b.feed_id
                WHERE b.deleted=0 AND bf.deleted=0 AND b.feed_id!=a.feed_id AND b.feed_id!='chrome'
                AND (b.added<a.added OR (b.added=a.added AND b.id<a.id))
                AND title_similarity(a.title,b.title)>=94))"""
        order = (
            "(SELECT max(e.created) FROM events e WHERE e.article_id=a.id AND e.kind='open') DESC"
            if view == "history"
            else "a.published " + ("ASC" if sort == "oldest" else "DESC")
        )
        counts = self.one(
            f"SELECT count(*) n, coalesce(sum(a.read=0),0) unread {ARTICLES} WHERE {where}", args
        )
        items = self.rows(
            f"""SELECT a.id,a.feed_id,a.title,a.url,a.author,a.published,a.read,a.starred,
          a.read_at,a.words,a.tags,a.duplicate,substr(a.text,1,190) excerpt,f.title feed_title,f.folder_id,
          EXISTS(SELECT 1 FROM json_each(a.enclosures) e WHERE json_extract(e.value,'$.type') LIKE 'audio/%'
            OR {AUDIO_LINK}) audio
          {ARTICLES} WHERE {where} ORDER BY {order},a.id LIMIT ? OFFSET ?""",
            [*args, max(1, min(int(limit), 300)), max(0, int(offset))],
        )
        for a in items:
            if a["feed_id"] == READING_LIST:
                a["feed_title"] = site_name(a["url"])
        return {"items": items, "total": counts["n"], "unread": counts["unread"]}

    def article(self, id):
        a = self.one(
            f"SELECT a.*,f.title feed_title,f.url feed_url,f.options {ARTICLES} WHERE a.id=?",
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
        a["extracting"] = bool(
            self.rows(
                "SELECT 1 FROM jobs WHERE kind='extract' AND status IN ('queued','running') AND json_extract(payload,'$.article_id')=?",
                (id,),
            )
        )
        if a["feed_id"] == READING_LIST:
            a["feed_title"] = site_name(a["url"])
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
        if not changes and not opened:
            return {"ok": True}
        undo = None
        with self.db:
            articles = [
                dict(self.db.execute("SELECT * FROM articles WHERE id=?", (id,)).fetchone() or {})
                for id in ids
            ]
            if any(not a for a in articles):
                raise ValueError("Article not found.")
            # Opening alone changes nothing to undo; it only goes into History.
            if changes:
                undo = self._undo(
                    "Article changes", [("articles", a["id"], {k: a[k] for k in changes}) for a in articles]
                )
            for a in articles:
                if changes:
                    self.db.execute(
                        "UPDATE articles SET " + ",".join(k + "=?" for k in changes) + " WHERE id=?",
                        [*changes.values(), a["id"]],
                    )
                if opened or (read is not None and bool(read) != bool(a["read"])):
                    self.db.execute(
                        "INSERT INTO events(article_id,feed_id,kind,created) VALUES(?,?,?,?)",
                        (a["id"], a["feed_id"], "open" if opened else "read" if read else "unread", now()),
                    )
        return {"ok": True, "undo": undo} if undo else {"ok": True}

    def mark_all(self, feed_id=None, folder_id=None, view="all", q="", tag=None, titles_only=False):
        where, args = self._where(
            feed_id=feed_id,
            folder_id=folder_id,
            view=view,
            q=q,
            tag=tag,
            unread=True,
            titles_only=titles_only,
        )
        # One snapshot, one transaction, regardless of stream size.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            articles = self.rows(
                f"SELECT a.id,a.read,a.read_at {ARTICLES} WHERE {where}",
                args,
            )
            ids = [a["id"] for a in articles]
            undo = self._undo(
                "Mark stream as read",
                [("articles", a["id"], {"read": a["read"], "read_at": a["read_at"]}) for a in articles],
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

    def save_rule(
        self, name, field, pattern, action, value="", feed_id=None, enabled=True, id=None, is_regex=True
    ):
        if field not in RULE_FIELDS or action not in RULE_ACTIONS:
            raise ValueError("Invalid rule field or action.")
        compiled = compile_rule(pattern, is_regex)
        if action == "rewrite":
            problem = replacement_error(compiled[0], value)
            if problem:
                raise ValueError(problem)
        if action == "tag" and not value.strip():
            raise ValueError("A tag rule needs a tag.")
        if feed_id:
            self.one("SELECT id FROM feeds WHERE id=?", (feed_id,))
        id = id or uid()
        with self.db:
            # Named columns, so adding one later cannot shift a positional insert.
            # The upsert keeps the rowid, and with it the rule's place in the order.
            self.db.execute(
                """INSERT INTO rules (id,name,field,pattern,action,value,feed_id,enabled,is_regex)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,field=excluded.field,pattern=excluded.pattern,action=excluded.action,
                value=excluded.value,feed_id=excluded.feed_id,enabled=excluded.enabled,
                is_regex=excluded.is_regex""",
                (id, name, field, pattern, action, value, feed_id, int(enabled), int(is_regex)),
            )
        return {"id": id}

    def validate_rule(self, pattern, is_regex=True, action="read", value="", field="title"):
        """Compile a draft with the engine that runs it, so the check cannot lie."""
        lines, problems = rule_lines(pattern), []
        for number, line in enumerate(lines, 1):
            try:
                compiled = regex.compile(line if is_regex else regex.escape(line), regex.I)
            except regex.error as error:
                problems.append({"line": number, "pattern": line, "error": str(error)})
                continue
            # A pattern still searching a small hostile string will never finish a library.
            try:
                compiled.search("a" * 4000 + "!", timeout=RULE_TIMEOUT / 2)
            except TimeoutError:
                problems.append({"line": number, "pattern": line, "slow": True})
        problem = None
        if action == "rewrite" and lines and not problems:
            problem = replacement_error(compile_rule(pattern, is_regex)[0], value)
        if action == "tag" and not (value or "").strip():
            problem = "A tag rule needs a tag."
        return {"lines": len(lines), "errors": problems, "value_error": problem}

    def delete_rule(self, id):
        with self.db:
            self.db.execute("DELETE FROM rules WHERE id=?", (id,))
        return {"ok": True}

    def matchers(self, rules=None):
        """Enabled rules with their lines compiled once, in execution order.

        Compiling here rather than per article is what makes a whole-library pass
        cheap. A rule that no longer compiles is skipped rather than raised: one
        bad row must not stop every article from being ingested.
        """
        ready = []
        for rule in self.rules() if rules is None else rules:
            if not rule["enabled"]:
                continue
            try:
                ready.append((rule, compile_rule(rule["pattern"], rule.get("is_regex", 1))))
            except ValueError:
                continue
        return ready

    @staticmethod
    def apply_matchers(a, matchers):
        """Run compiled rules over one article, reporting what actually changed.

        Pure: it neither sanitizes nor writes, so a preview and a real pass agree
        by construction. A rule counts as fired only when it altered the article,
        which is what lets the preview promise "would change" and not "matches".
        The two are reported separately, because a rule that matches while
        changing nothing is already done, not misspelt.
        Returns the columns that changed, the rules that changed them, the rules
        whose pattern hit at all, and any that ran out of time on this article.
        """
        changed, fired, matched, slow = set(), [], [], []
        for rule, patterns in matchers:
            if rule["feed_id"] not in (None, a["feed_id"]):
                continue
            source = str(a.get(rule["field"], ""))
            try:
                if not any(p.search(source, timeout=RULE_TIMEOUT) for p in patterns):
                    continue
                if rule["action"] == "rewrite":
                    body = a["body"]
                    for p in patterns:
                        body = p.sub(rule["value"], body, timeout=RULE_TIMEOUT)
            except TimeoutError:
                # This rule cannot finish on this article. Skip the pair, not the
                # article: the other rules still have work to do here.
                slow.append(rule["id"])
                continue
            matched.append(rule["id"])
            if rule["action"] == "rewrite":
                if body == a["body"]:
                    continue
                a["body"] = body
                changed.add("body")
            elif rule["action"] == "tag":
                tags = sorted(set(a["tags"] + [rule["value"]]))
                if tags == a["tags"]:
                    continue
                a["tags"] = tags
                changed.add("tags")
            else:
                column = RULE_STATE[rule["action"]]
                if a[column]:
                    continue
                a[column] = 1
                changed.add(column)
            fired.append(rule["id"])
        return changed, fired, matched, slow

    def apply_rules(self, a, matchers=None):
        """Ingest path: run the rules, then re-derive the stored body and text.

        The sanitize belongs to freshly fetched publisher HTML, not to the rules,
        which is why the stored-article pass below can skip it when no body changed.
        """
        self.apply_matchers(a, self.matchers() if matchers is None else matchers)
        a["body"] = sanitize(a["body"], a["url"])
        a["text"] = plain(a["body"])
        a["words"] = len(a["text"].split()) + len(a.get("transcript", "").split())
        return a

    def ingest(self, *args, **kwargs):
        return self.ingest_new(*args, **kwargs)[0]

    def ingest_new(self, *args, **kwargs):
        """Ingest an entry and say whether it was a new article, not an update."""
        # Serialize lookup + content/rule updates with interactive state edits.
        # No network requests happen while this short write transaction is held.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._ingest(*args, **kwargs)

    def _ingest(
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
        read=False,
        duration=0,
    ):
        # Publisher links reach window.open and <a href>; keep only web URLs.
        url = clean_url(url) if urlsplit(url).scheme in ("http", "https") else ""
        if enclosures is not None:
            enclosures = [e for e in enclosures if urlsplit(e.get("href") or "").scheme in ("http", "https")]
        if body and not looks_like_html(body):  # e.g. a YouTube description, from any source
            body = text_html(body)
        body = sanitize(body, url)
        digest = fingerprint(title, plain(body))
        guid = guid or url or digest
        if self.rows("SELECT 1 FROM tombstones WHERE feed_id=? AND guid=?", (feed_id, guid)):
            return None, False
        # The same item under its id first, then the same content under a new id,
        # then the same link. A title alone is not content: linkless entries and
        # saved pages that merely share a <title> stay separate.
        existing = self.rows(
            """SELECT * FROM articles WHERE feed_id=?
              AND (guid=? OR (fingerprint=? AND text!='') OR (url=? AND url!=''))
              ORDER BY (guid=?) DESC, (fingerprint=?) DESC LIMIT 1""",
            (feed_id, guid, digest, url, guid, digest),
        )
        # Some podcast feeds link every episode to the show's home page; two
        # episodes with their own ids and different audio are not one article.
        if existing and existing[0]["guid"] != guid and existing[0]["fingerprint"] != digest:
            audio = {e.get("href") for e in enclosures or []}
            theirs = {e.get("href") for e in existing[0]["enclosures"]}
            if audio and theirs and audio.isdisjoint(theirs):
                existing = []
        if existing:
            a = existing[0]
            previous = a.copy()
            if transcript and not a["transcript"]:
                a["transcript"] = transcript
                a["words"] += len(transcript.split())
            if enclosures is not None:
                a["enclosures"] = enclosures
            if duration and not a["duration"]:
                a["duration"] = duration
            if a["guid"] == guid:
                a.update(
                    title=title or a["title"],
                    author=author or a["author"],
                    # A date still in the future keeps the one it was stored with.
                    published=(not_future(published) == published and published) or a["published"],
                )
                if not a["extracted"]:
                    a["url"] = url or a["url"]
            if a["guid"] == guid and a["fingerprint"] != digest:
                a.update(title=title or a["title"], author=author or a["author"], fingerprint=digest)
                if not a["extracted"]:
                    a["body"] = body
                a = self.apply_rules(a)
            if a != previous:
                self._write_article(
                    a,
                    "title author body text words fingerprint tags read starred "
                    "deleted url published enclosures transcript duration".split(),
                )
            return a["id"], False
        # A feed set to arrive read keeps its articles without asking for attention.
        feed = self.db.execute("SELECT options FROM feeds WHERE id=?", (feed_id,)).fetchone()
        read = read or bool(feed and json.loads(feed["options"]).get("mark_read"))
        a = dict(
            id=uid(),
            feed_id=feed_id,
            guid=guid,
            url=url,
            title=title or "(Untitled)",
            author=author or "",
            published=not_future(published) or now(),
            added=now(),
            body=body,
            text=plain(body),
            transcript=transcript,
            enclosures=enclosures or [],
            tags=tags or [],
            read=int(read),
            starred=0,
            deleted=0,
            duplicate=0,
            read_at=None,
            words=0,
            fingerprint=digest,
            extracted=0,
            state_changed=None,
            duration=duration,
        )
        if len(title) >= 20:
            recent = self.rows(
                "SELECT title,feed_id FROM articles WHERE published>? AND deleted=0 ORDER BY published DESC LIMIT 1000",
                (now(days=-7),),
            )
            a["duplicate"] = int(
                any(
                    x["feed_id"] != feed_id and ratio(title.casefold(), x["title"].casefold()) >= 94
                    for x in recent
                )
            )
        a = self.apply_rules(a)
        self.db.execute(
            "INSERT INTO articles (" + ",".join(a) + ") VALUES (" + ",".join("?" for _ in a) + ")",
            [json.dumps(v) if k in JSON_FIELDS else v for k, v in a.items()],
        )
        return a["id"], True

    def _write_article(self, a, columns):
        """Store the named columns of an article dict; JSON fields encode themselves."""
        self.db.execute(
            "UPDATE articles SET " + ",".join(c + "=?" for c in columns) + " WHERE id=?",
            [json.dumps(a[c]) if c in JSON_FIELDS else a[c] for c in columns] + [a["id"]],
        )

    def enqueue(self, kind, payload=None):
        if kind not in ("refresh", "extract", "download", "backfill", "purge", "rules"):
            raise ValueError("Unknown job type.")
        payload = payload or {}
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._enqueue(kind, payload)

    def _enqueue(self, kind, payload):
        if kind == "backfill":
            if set(payload) - {"feed_id", "url", "max_pages"}:
                raise ValueError("Unknown backfill option.")
            self.one("SELECT id FROM feeds WHERE id=? AND deleted=0", (payload.get("feed_id"),))
            if payload.get("url"):
                safe_url(payload["url"])
            pending = self.rows(
                "SELECT id FROM jobs WHERE kind='backfill' AND json_extract(payload,'$.feed_id')=? AND status IN ('queued','running','paused','failed') ORDER BY created DESC LIMIT 1",
                (payload["feed_id"],),
            )
            if pending:
                self.db.execute(
                    "UPDATE jobs SET status='queued' WHERE id=? AND status IN ('paused','failed')",
                    (pending[0]["id"],),
                )
                return pending[0]
        if kind == "refresh":
            if set(payload) - {"feed_id", "manual"}:
                raise ValueError("Unknown refresh option.")
            # A feed the user asks for now jumps ahead of a queued library-wide
            # refresh: promote its pending job rather than adding a second one.
            # One refresh per feed at a time: a second request only raises the
            # urgency of the one already queued. A scheduled check adds nothing
            # to one that is running; a request made now fetches again after it.
            if payload.get("feed_id"):
                urgency = payload.get("manual") or 0
                pending = self.rows(
                    "SELECT id,payload,status FROM jobs WHERE kind='refresh' AND json_extract(payload,'$.feed_id')=?"
                    " AND status IN ('queued','running') ORDER BY status='queued' DESC",
                    (payload["feed_id"],),
                )
                queued = [j for j in pending if j["status"] == "queued"]
                if queued and urgency > (queued[0]["payload"].get("manual") or 0):
                    # Its turn is now: after anything promoted before it.
                    self.db.execute(
                        "UPDATE jobs SET payload=?,updated=? WHERE id=?",
                        (json.dumps(payload, sort_keys=True), now(), queued[0]["id"]),
                    )
                if queued or (pending and not urgency):
                    return {"id": pending[0]["id"]}
        # An extraction someone is waiting for (an open article) goes ahead of
        # the backlog, e.g. a thousand pages from a Reading List import.
        asked = kind == "extract" and bool(payload.pop("asked", False))
        encoded = json.dumps(payload, sort_keys=True)
        pending = self.rows(
            "SELECT id FROM jobs WHERE kind=? AND payload=? AND status IN ('queued','running')",
            (kind, encoded),
        )
        if pending:
            if asked:
                self.db.execute(
                    "UPDATE jobs SET result=json_set(coalesce(result,'{}'),'$.asked',1) WHERE id=?",
                    (pending[0]["id"],),
                )
            return pending[0]
        id = uid()
        self.db.execute(
            "INSERT INTO jobs(id,kind,payload,created,updated,result) VALUES(?,?,?,?,?,?)",
            (id, kind, encoded, now(), now(), json.dumps({"asked": 1}) if asked else None),
        )
        return {"id": id}

    def recent_dates(self, feed_id):
        return [
            aware(a["published"])
            for a in self.rows(
                "SELECT published FROM articles WHERE feed_id=? ORDER BY published DESC LIMIT 25", (feed_id,)
            )
        ]

    def refresh_interval(self, feed_id, options, dates=None):
        if options.get("interval"):
            return int(options["interval"])
        dates = self.recent_dates(feed_id) if dates is None else dates
        since = datetime.now(timezone.utc) - timedelta(days=RHYTHM_DAYS)
        dates = [d for d in dates if d > since]
        gaps = [(a - b).total_seconds() / 60 for a, b in zip(dates, dates[1:]) if a > b]
        return int(
            max(MIN_INTERVAL, min(MAX_INTERVAL, statistics.median(gaps) / 2)) if gaps else MAX_INTERVAL
        )

    # Whether the network itself is down, remembered briefly so a pass over
    # hundreds of feeds asks once rather than once per feed.
    network_checked = (0.0, True)

    def network_up(self, failing):
        checked, up = Core.network_checked
        if time.monotonic() - checked < 30:
            return up
        hosts = {
            urlsplit(f["url"]).hostname
            for f in self.rows(
                "SELECT url FROM feeds WHERE deleted=0 AND url LIKE 'http%' AND error IS NULL"
                " AND checked IS NOT NULL ORDER BY checked DESC LIMIT 20"
            )
        } - {urlsplit(failing).hostname, None}
        up = not hosts
        for host in list(hosts)[:3]:
            try:
                socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                up = True
                break
            except OSError:
                continue
        Core.network_checked = (time.monotonic(), up)
        return up

    def refresh(self, feed_id):
        f = self.one("SELECT * FROM feeds WHERE id=? AND deleted=0", (feed_id,))
        if not f["url"].startswith("http"):
            return {"new": 0}
        # A new subscription starts read: its existing posts are history, not a backlog.
        read = self.settings()["new_feed_read"] and not self.rows(
            "SELECT 1 FROM articles WHERE feed_id=? LIMIT 1", (feed_id,)
        )
        extract = self.extracts(f["options"])
        try:
            options = request_options(f)
            if options.get("scrape_selector"):
                new = self.scrape(f, read, extract)
            else:
                moved = []
                durations = {}
                with self.reader(options, moved, durations) as reader:
                    reader.add_feed(f["url"], exist_ok=True)
                    reader.update_feed(f["url"])
                    rf = reader.get_feed(f["url"])
                    new = 0
                    for entry in reader.get_entries(feed=f["url"]):
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
                        id, added = self.ingest_new(
                            feed_id, **entry_fields(entry, durations), transcript=transcript, read=read
                        )
                        if added:
                            new += 1
                            if extract and id:
                                self.enqueue("extract", {"article_id": id})
                    with self.db:
                        self.db.execute(
                            "UPDATE feeds SET title=CASE WHEN json_extract(options,'$.custom_title') THEN title ELSE ? END,site_url=? WHERE id=?",
                            (
                                rf.title or f["title"],
                                rf.link or "",
                                feed_id,
                            ),
                        )
                    # Follow a permanent move unless the destination is already a
                    # subscription (a duplicate in an import, or a blog that moved
                    # onto a feed the user also subscribed to): the old URL keeps
                    # redirecting, and renaming would only fail on the unique URL.
                    # An unsubscribed feed at the destination (the duplicate, deleted)
                    # does not block the move: it takes the old URL instead, so
                    # restoring it still gives the duplicate it was.
                    holders = moved and self.rows("SELECT id,deleted FROM feeds WHERE url=?", (moved[-1],))
                    if moved and moved[-1] != f["url"] and all(h["deleted"] for h in holders):
                        safe_url(moved[-1])
                        if holders:
                            reader.delete_feed(moved[-1], missing_ok=True)
                        reader.change_feed_url(f["url"], moved[-1])
                        with self.db:
                            if origin(f["url"]) != origin(moved[-1]):
                                self.db.execute(
                                    "UPDATE feeds SET options=json_remove(options,'$.cookie') WHERE id=?",
                                    (feed_id,),
                                )
                            for h in holders:
                                self.db.execute(
                                    "UPDATE feeds SET url=? WHERE id=?", ("moving:" + uid(), h["id"])
                                )
                            self.db.execute("UPDATE feeds SET url=? WHERE id=?", (moved[-1], feed_id))
                            for h in holders:
                                self.db.execute("UPDATE feeds SET url=? WHERE id=?", (f["url"], h["id"]))
            dates = self.recent_dates(feed_id)
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                current = self.one("SELECT options FROM feeds WHERE id=?", (feed_id,))
                interval = self.refresh_interval(feed_id, current["options"], dates)
                self.db.execute(
                    "UPDATE feeds SET checked=?,next_check=?,last_article=?,interval=?,error=NULL,failures=0 WHERE id=?",
                    (
                        now(),
                        # Jitter keeps feeds refreshed together from staying in
                        # lockstep; an interval the user chose is kept exact.
                        now(
                            minutes=interval
                            * (1 if current["options"].get("interval") else random.uniform(0.9, 1.1))
                        ),
                        dates[0].isoformat() if dates else None,
                        interval,
                        feed_id,
                    ),
                )
            return {"new": new}
        except Exception as error:
            if unreachable(error) and not self.network_up(f["url"]):
                # Offline (just woken from sleep, say): nobody failed, so try
                # again in a minute without counting it against the feed.
                with self.db:
                    self.db.execute("UPDATE feeds SET next_check=? WHERE id=?", (now(minutes=1), feed_id))
                return {"new": 0, "offline": True}
            retry = now(minutes=min(1440, 15 * 2 ** min(f["failures"], 7)))
            # A publisher asking us to slow down gets at least the wait it named.
            pushed = next(
                (
                    e
                    for e in chain(error)
                    if getattr(getattr(e, "response", None), "status_code", 0) in (429, 503)
                ),
                None,
            )
            if pushed is not None:
                retry = max(retry, retry_at(pushed, f["failures"] + 1).isoformat())
            with self.db:
                self.db.execute(
                    "UPDATE feeds SET checked=?,error=?,failures=failures+1,next_check=? WHERE id=?",
                    (now(), str(error)[:1000], retry, feed_id),
                )
            raise

    def extracts(self, options):
        """Per-feed extract option, or the global default when the feed has none."""
        value = options.get("extract")
        return self.settings()["extract_articles"] if value is None else bool(value)

    def scrape(self, feed, read=False, extract=False):
        data, url, _ = fetch(feed["url"], request_options(feed))
        soup = BeautifulSoup(data, "html.parser")
        items = soup.select(feed["options"]["scrape_selector"])
        if not items:
            raise ValueError("Site scraper matched no items; check the CSS selector.")
        count = 0
        for item in items[:2000]:
            link = item.select_one(feed["options"].get("scrape_link") or "a[href]")
            if not link and item.name == "a":
                link = item
            if link and link.get("href"):
                article_url = urljoin(url, link["href"])
                id, added = self.ingest_new(
                    feed["id"],
                    guid=article_url,
                    title=link.get_text(" ", strip=True),
                    url=article_url,
                    body=str(item),
                    published=scraped_date(item),
                    read=read,
                )
                if added:
                    count += 1
                    if extract:
                        self.enqueue("extract", {"article_id": id})
        return count

    def extract_article(self, article_id):
        a = self.article(article_id)
        if not a["source_url"]:
            raise ValueError("This article has no source page URL to extract.")
        f = self.one("SELECT * FROM feeds WHERE id=?", (a["feed_id"],))
        video = probe_video(a["url"], request_options(f)) if video_id(a["url"]) else None
        result = None if video else extract(a["url"], request_options(f))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            # Fetching can take seconds: merge content into current user state,
            # never the snapshot from before the request.
            a = self.one("SELECT * FROM articles WHERE id=?", (article_id,))
            if video:  # The description is the article; only the kind of video is learned.
                a["url"] = video["url"]
                a["duration"] = video["duration"]
                if video["short"]:
                    a["tags"] = sorted(set(a["tags"] + ["short"]))
            else:
                a.update(
                    body=result["body"], url=result["url"], transcript=result["transcript"] or a["transcript"]
                )
                if result["paywall"]:
                    a["tags"] = sorted(set(a["tags"] + ["paywall"]))
            a = self.apply_rules(a)
            a["extracted"] = 1
            self._write_article(
                a,
                "extracted body text url transcript tags words read starred deleted duration".split(),
            )
        return {"id": article_id}

    def download(self, article_id, url, job_id):
        a = self.article(article_id)
        if not any(e["href"] == url and (e.get("type") or "").startswith("audio/") for e in a["enclosures"]):
            raise ValueError("Only audio enclosures belonging to this article can be downloaded.")
        # Bounded, streamed download. Atomic rename exposes only complete episodes.
        safe_url(url)

        path = self.root / "downloads" / job_id
        partial = path.with_suffix(".part")
        try:
            with http_client(timeout=60) as client:
                for _ in range(10):
                    safe_url(url)
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers["location"])
                            continue
                        response.raise_for_status()
                        size = 0
                        limit = int(os.getenv("FEEDELIO_MAX_EPISODE_MB", "500")) * 1024 * 1024
                        with partial.open("wb") as output:
                            for chunk in bounded(response.iter_bytes(), limit, "Episode"):
                                size += len(chunk)
                                output.write(chunk)
                        partial.replace(path)
                        return {"bytes": size}
            raise ValueError("Too many redirects.")
        finally:
            partial.unlink(missing_ok=True)

    def download_path(self, id):
        self.one("SELECT id FROM jobs WHERE id=? AND kind='download' AND status='done'", (id,))
        if not is_uid(id):
            raise ValueError("Invalid download id.")
        return self.root / "downloads" / id

    def delete_download(self, id):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            path = self.download_path(id)
            path.unlink(missing_ok=True)
            self.db.execute("UPDATE jobs SET status='removed' WHERE id=?", (id,))
        return {"ok": True}

    def _archive_fetch(self, url, options, json=None, headers=None):
        """Use reader's HTTP stack, with the same bounds and credential policy."""
        with requests.Session() as session:
            configure_session(session, options)
            session.max_redirects = 10
            method = "GET" if json is None else "POST"
            with session.request(
                method, url, json=json, headers=headers, stream=True, timeout=(5, 30)
            ) as response:
                response.raise_for_status()
                data = b"".join(bounded(response.iter_content(65536), 8_000_000, "Archive response"))
                return data, response.url, response.headers

    def backfill(self, feed_id, url=None, max_pages=None, *, state=None):
        """One resumable archive step, never an unbounded network loop.

        max_pages is accepted for old queued jobs, but no longer truncates history.
        The worker, not this parser step, owns pacing and retries.
        """
        f = self.one("SELECT * FROM feeds WHERE id=? AND deleted=0", (feed_id,))
        state = archives.initial(f, url) | (state or {})
        state["pending"] = list(state.get("pending", []))
        options = request_options(f)
        read = self.settings()["new_feed_read"]
        # Skip entries already supplied by the live feed without fetching again.
        while state["pending"]:
            post = state["pending"][0]
            if not self.rows(
                "SELECT id FROM articles WHERE feed_id=? AND (guid=? OR url=?)",
                (feed_id, post["guid"], clean_url(post["url"])),
            ) and not self.rows(
                "SELECT 1 FROM tombstones WHERE feed_id=? AND guid=?", (feed_id, post["guid"])
            ):
                break
            state["pending"].pop(0)
        if state["pending"] and state["mode"] == "youtube":
            # The watch page is the source: the listing only knows an id and a title.
            post = state["pending"][0]
            video = probe_video(post["url"], options)
            id = self.ingest(
                feed_id,
                post["guid"],
                video["title"] or post["title"],
                video["url"],
                text_html(video["description"]),
                video["author"],
                video["published"],
                tags=["short"] if video["short"] else [],
                read=read,
            )
            if id:
                with self.db:
                    self.db.execute(
                        "UPDATE articles SET extracted=1,duration=? WHERE id=?", (video["duration"], id)
                    )
                state["articles"] += 1
            state["pending"].pop(0)
            if not state["pending"] and not state["next_url"]:
                state["message"] = "All exposed archive pages processed."
            return state
        if state["pending"]:
            post = state["pending"][0]
            preview = False
            try:
                document = self._archive_fetch(post["url"], options)
            except requests.HTTPError as exc:
                if exc.response.status_code not in (404, 410):
                    raise
                document = None  # A removed post must not strand the remaining archive.
            # No optional transcript subrequests: one page is this entire step.
            try:
                if document is None:
                    raise ValueError("Article is no longer available.")
                article = extract(post["url"], options, document=document, transcripts=False)
            except ValueError:
                article = dict(body=text_html(post["summary"]), transcript="", paywall=False)
                preview = True
                state["previews"] = state.get("previews", 0) + 1
            id = self.ingest(
                feed_id,
                post["guid"],
                post["title"],
                post["url"],
                article["body"],
                published=post["published"],
                transcript=article["transcript"],
                tags=list(
                    set(
                        post["tags"]
                        + (["paywall"] if article["paywall"] else [])
                        + (["archive-preview"] if preview else [])
                    )
                ),
                read=read,
            )
            if id:
                with self.db:
                    self.db.execute("UPDATE articles SET extracted=? WHERE id=?", (int(not preview), id))
                state["articles"] += 1
            state["pending"].pop(0)
            return state
        url = state["next_url"]
        if not url:
            return state
        # A YouTube continuation is a token posted to one endpoint, so the token is the page.
        page = state.get("continuation") or url
        if page in state["visited"]:
            raise ValueError("Archive pagination repeats a page; stopped to avoid a request loop.")
        try:
            data, final, headers = self._archive_fetch(
                url,
                options,
                archives.youtube_request(state.get("continuation")),
                {"Cookie": "SOCS=CAI"} if state["mode"] == "youtube" else None,
            )
        except requests.HTTPError as exc:
            if state["pages"] == 0 or exc.response.status_code not in (404, 410):
                raise
            # Synthesised (WordPress ?paged=) and stale next links end with a 404.
            state.update(next_url=None, message="All exposed archive pages processed.")
            return state
        if state["mode"] == "youtube":
            state["pending"], state["continuation"] = archives.youtube_listing(data)
            next_url = archives.YOUTUBE_BROWSE if state["continuation"] else None
        elif state["mode"] == "substack":
            pending, next_url = archives.substack_page(data, final, state["offset"])
            if pending and all(p["guid"] in state.get("seen_posts", []) for p in pending):
                raise ValueError("Archive repeats the same posts; pagination may not be supported.")
            state["seen_posts"] = list(set(state.get("seen_posts", []) + [p["guid"] for p in pending]))
            state["pending"] = pending
            state["offset"] += len(pending)
        else:
            durations = {}
            entries, next_url = archives.feed_page(data, final, headers, durations)
            for e in entries:
                state["articles"] += self.ingest_new(feed_id, **entry_fields(e, durations), read=read)[1]
        state["visited"] = [*state["visited"], page]
        state["pages"] += 1
        state["next_url"] = next_url
        if not next_url and not state["pending"]:
            state["message"] = (
                "All exposed archive pages processed."
                if state["pages"] > 1 or state["mode"] == "youtube"
                else "This feed exposes no older-page link. Set an archive feed URL if the publisher provides one."
            )
        return state

    def control_backfill(self, id, action):
        if action not in ("pause", "resume"):
            raise ValueError("Choose pause or resume.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            job = self.one("SELECT * FROM jobs WHERE id=? AND kind='backfill'", (id,))
            if job["status"] == "done":
                raise ValueError("This archive is already complete.")
            self.db.execute(
                "UPDATE jobs SET status=?,updated=? WHERE id=?",
                ("paused" if action == "pause" else "queued", now(), id),
            )
            if action == "resume":
                self.db.execute(
                    "UPDATE jobs SET result=json_set(coalesce(result,'{}'),'$.attempts',0) WHERE id=?",
                    (id,),
                )
        return {"ok": True}

    def _run_backfill(self, job):
        state = job["result"] or {}
        try:
            state = self.backfill(**job["payload"], state=state)
            state["attempts"] = 0
            state["not_before"] = self._archive_pause(job).isoformat()
            status = "queued" if state["next_url"] or state["pending"] else "done"
            error = None
        except Exception as exc:
            state["attempts"] = state.get("attempts", 0) + 1
            response = getattr(exc, "response", None)
            code = response.status_code if response is not None else None
            retry = (
                code in (429, 503)
                or (code is not None and code >= 500)
                or isinstance(exc, requests.ConnectionError)
                or isinstance(exc, requests.Timeout)
            )
            state["not_before"] = retry_at(exc, state["attempts"]).isoformat()
            status = "queued" if retry and state["attempts"] < 5 else "failed"
            error = str(exc)[:1000]
        with self.db:
            # A pause issued during the network call must survive its completion.
            self.db.execute(
                "UPDATE jobs SET status=CASE WHEN status='paused' THEN status ELSE ? END,updated=?,result=?,error=? WHERE id=?",
                (status, now(), json.dumps(state), error, job["id"]),
            )
            self._pace_host(job | {"result": state}, state["not_before"])

    def _archive_hosts(self):
        """Sites with an archive request still pending their minute, past deadlines dropped."""
        hosts = self.rows("SELECT value FROM settings WHERE key='archive_hosts'")
        hosts = json.loads(hosts[0]["value"]) if hosts else {}
        return {h: t for h, t in hosts.items() if t > now()}

    def _archive_pause(self, job):
        host = archives.host(job["result"], job.get("feed_url"))
        return datetime.now(timezone.utc) + timedelta(seconds=archives.interval(host))

    def _setting(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def _pace_host(self, job, until):
        hosts = self._archive_hosts()
        hosts[archives.host(job["result"], job.get("feed_url"))] = until
        self._setting("archive_hosts", hosts)

    def purge(self):
        count = 0
        with self.reader() as reader:
            for row in self.rows("SELECT id FROM feeds WHERE deleted=0"):
                count += self._purge_feed(reader, row["id"])
        with self.db:
            self.db.execute(
                "DELETE FROM undo WHERE created<?",
                (now(days=-7),),
            )
        return {"purged": count}

    def _purge_feed(self, reader, feed_id):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.rows("SELECT * FROM feeds WHERE id=? AND deleted=0", (feed_id,))
            if not current:
                return 0
            f = current[0]
            days = f["options"].get("retention_days")
            days = self.settings()["retention_days"] if days is None else days
            if f["options"].get("archive") or not days:
                return 0
            cutoff = now(days=-int(days))
            doomed = self.rows(
                "SELECT id,guid FROM articles WHERE feed_id=? AND added<? AND starred=0 AND read=1",
                (f["id"], cutoff),
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO tombstones VALUES (?,?)", [(f["id"], a["guid"]) for a in doomed]
            )
            # An episode's file goes with its article, or retention would never free the disk.
            for a in doomed:
                for j in self.rows(
                    "SELECT id FROM jobs WHERE kind='download' AND status='done' AND json_extract(payload,'$.article_id')=?",
                    (a["id"],),
                ):
                    (self.root / "downloads" / j["id"]).unlink(missing_ok=True)
                    self.db.execute("UPDATE jobs SET status='removed' WHERE id=?", (j["id"],))
            self.db.executemany("DELETE FROM articles WHERE id=?", [(a["id"],) for a in doomed])
        # Public delete_entry only accepts manually added entries. This is
        # the same pinned internal primitive reader's retention plugins use.
        reader._storage.delete_entries(
            [
                (f["url"], a["guid"])
                for a in doomed
                if reader.get_entry((f["url"], a["guid"]), None) is not None
            ]
        )
        return len(doomed)

    def tick(self, refreshes=True, slots=None):
        """Schedule due work and run one job.

        The worker's main thread passes refreshes=False and leaves feeds to its
        refresh threads, which pass refreshes="only" and share one HostSlots.
        """
        if refreshes != "only":
            self.schedule()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            # Keeping feeds current comes first: after a large import thousands of
            # extractions are queued, and new posts must not wait behind them.
            # Archive pages are throttled to one a minute per site, so they cannot
            # starve the extraction backlog, and a hundred blogs queued together
            # still advance side by side instead of taking turns at one a minute.
            hosts = self._archive_hosts()
            job = None
            for candidate in self.rows(
                """
                SELECT j.*,f.url feed_url FROM jobs j
                LEFT JOIN feeds f ON f.id=json_extract(j.payload,'$.feed_id') AND f.deleted=0
                WHERE status='queued' AND coalesce(json_extract(result,'$.not_before'),'')<=?
                AND (kind!='backfill' OR f.id IS NOT NULL)
                AND CASE WHEN kind='refresh' THEN ? ELSE ? END
                ORDER BY CASE kind WHEN 'extract' THEN 3 - 4*coalesce(json_extract(result,'$.asked'),0) WHEN 'download' THEN 2 WHEN 'backfill' THEN 1
                  WHEN 'refresh' THEN -coalesce(json_extract(payload,'$.manual'),0) ELSE 0 END,
                  updated,created
            """,
                (now(), bool(refreshes), refreshes != "only"),
            ):
                if (
                    candidate["kind"] == "backfill"
                    and archives.host(candidate["result"], candidate["feed_url"]) in hosts
                ):
                    continue
                site = urlsplit(candidate["feed_url"] or "").hostname
                if candidate["kind"] == "refresh" and slots and not slots.take(site):
                    continue
                job = candidate
                break
            if job is None:
                return False
            self.db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (now(), job["id"]))
            if job["kind"] == "backfill":
                self._pace_host(job, self._archive_pause(job).isoformat())
        try:
            return self._run(job)
        finally:
            if job["kind"] == "refresh" and slots:
                slots.release(urlsplit(job["feed_url"] or "").hostname)

    def schedule(self):
        """Queue the refreshes that are due, the nightly purge and archive seeding."""
        with self.db:
            self._setting("worker_heartbeat", now())
        for f in self.rows(
            "SELECT id FROM feeds WHERE deleted=0 AND url LIKE 'http%' AND (next_check IS NULL OR next_check<=?)",
            (now(),),
        ):
            self.enqueue("refresh", {"feed_id": f["id"]})
        if not self.rows(
            "SELECT id FROM jobs WHERE kind='purge' AND created>?",
            (now(days=-1),),
        ):
            self.enqueue("purge")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            # Seed existing subscriptions too, once, after a successful refresh.
            for feed in self.rows("""
                SELECT id FROM feeds f WHERE deleted=0 AND checked IS NOT NULL AND error IS NULL
                AND url LIKE 'http%' AND coalesce(json_extract(options,'$.scrape_selector'),'')=''
                AND NOT EXISTS (SELECT 1 FROM jobs j WHERE kind='backfill'
                    AND json_extract(j.payload,'$.feed_id')=f.id)
            """):
                self._enqueue("backfill", {"feed_id": feed["id"]})

    def _run(self, job):
        try:
            p = job["payload"]
            if job["kind"] == "refresh":
                result = self.refresh(p["feed_id"]) if p.get("feed_id") else self.refresh_all()
            elif job["kind"] == "extract":
                result = self.extract_article(**p)
            elif job["kind"] == "download":
                result = self.download(**p, job_id=job["id"])
            elif job["kind"] == "backfill":
                self._run_backfill(job)
                return True
            elif job["kind"] == "rules":
                result = self.reapply_rules(job_id=job["id"])
            else:
                result = self.purge()
            with self.db:
                self.db.execute(
                    "UPDATE jobs SET status='done',updated=?,result=? WHERE id=?",
                    (now(), json.dumps(result), job["id"]),
                )
        except Exception as error:
            # A publisher rate-limiting a burst of extractions (twenty new posts
            # from one host after an import) gets a later retry, not a lost article.
            code = getattr(getattr(error, "response", None), "status_code", None)
            attempts = (job["result"] or {}).get("attempts", 0) + 1
            retry = job["kind"] == "extract" and code in (429, 503) and attempts < 4
            with self.db:
                self.db.execute(
                    "UPDATE jobs SET status=?,updated=?,error=?,result=? WHERE id=?",
                    (
                        "queued" if retry else "failed",
                        now(),
                        str(error)[:1000],
                        json.dumps(dict(attempts=attempts, not_before=retry_at(error, attempts).isoformat()))
                        if retry
                        else None,
                        job["id"],
                    ),
                )
        return True

    def refresh_all(self):
        # Asked for now, so ahead of scheduled work; the longest-waiting first,
        # so a feed late in the alphabet is not always last.
        for f in self.rows(
            "SELECT id FROM feeds WHERE deleted=0 AND id!=? AND url LIKE 'http%'"
            " ORDER BY checked IS NOT NULL, checked, rowid",
            (READING_LIST,),
        ):
            self.enqueue("refresh", {"feed_id": f["id"], "manual": EVERY_FEED})
        return {"queued": True}

    def scan_rules(self, matchers, write, batch=200, job_id=None):
        """Walk every stored article once, storing the changes only when asked.

        With write=False nothing is written and the tally is identical, so the
        preview is this same pass without its writes -- the estimate cannot drift
        from what applying actually does.
        """
        totals = dict.fromkeys(RULE_ACTIONS, 0)
        per_rule = {rule["id"]: 0 for rule, _ in matchers}
        per_rule_matched = {rule["id"]: 0 for rule, _ in matchers}
        rules_by_id = {rule["id"]: rule for rule, _ in matchers}
        scanned = changed_articles = matched_articles = timeouts = 0
        slow = set()
        # Deleted articles stay out of reach, as they already are for ingest.
        ids = [row["id"] for row in self.rows("SELECT id FROM articles WHERE deleted=0")]
        for start in range(0, len(ids), batch):
            chunk = ids[start : start + batch]
            sql = "SELECT * FROM articles WHERE id IN (%s) AND deleted=0" % ",".join("?" for _ in chunk)
            with ExitStack() as scope:
                if write:
                    # R4: read each article's current state inside the transaction
                    # that writes it, so a concurrent edit cannot be clobbered.
                    scope.enter_context(self.db)
                    self.db.execute("BEGIN IMMEDIATE")
                for a in self.rows(sql, chunk):
                    scanned += 1
                    fields, fired, hit, stalled = self.apply_matchers(a, matchers)
                    if stalled:
                        timeouts += 1
                        slow.update(stalled)
                    if hit:
                        matched_articles += 1
                        for rule_id in hit:
                            per_rule_matched[rule_id] += 1
                    if not fields:
                        continue
                    changed_articles += 1
                    for rule_id in fired:
                        per_rule[rule_id] += 1
                        totals[rules_by_id[rule_id]["action"]] += 1
                    if "body" in fields:
                        # A rewrite value is arbitrary text landing in stored HTML.
                        a["body"] = sanitize(a["body"], a["url"])
                        a["text"] = plain(a["body"])
                        a["words"] = len(a["text"].split()) + len(a.get("transcript", "").split())
                        fields |= {"text", "words"}
                    if write:
                        # Only the columns that moved, so the article_search trigger
                        # re-indexes a row only when its text really changed.
                        self._write_article(a, sorted(fields))
            if job_id:
                with self.db:
                    self.db.execute(
                        "UPDATE jobs SET result=?,updated=? WHERE id=?",
                        (json.dumps({"processed": scanned, "total": len(ids)}), now(), job_id),
                    )
        return {
            "processed": scanned,
            "changed": changed_articles,
            # A rule can match without changing anything, when the article is
            # already read, starred or tagged. Telling those apart is what keeps
            # "nothing would change" from reading as "your pattern is wrong".
            "matched": matched_articles,
            "actions": totals,
            "rules": [
                {
                    "id": id,
                    "name": rules_by_id[id]["name"],
                    "changed": count,
                    "matched": per_rule_matched[id],
                }
                for id, count in per_rule.items()
            ],
            "timeouts": timeouts,
            "slow": sorted(slow),
        }

    def reapply_rules(self, job_id=None):
        return self.scan_rules(self.matchers(), write=True, job_id=job_id)

    def preview_rules(self, rule=None):
        """Exactly what applying would change, changing nothing.

        Given one rule -- stored, or still a draft in the editor -- only that rule
        is scanned, so a pattern can be tried before it is saved. Either way it is
        the same pass as applying, so the count cannot drift from what applying does.
        """
        if rule is None:
            return self.scan_rules(self.matchers(), write=False)
        draft = {
            "id": rule.get("id") or "draft",
            "name": rule.get("name") or "This rule",
            "field": rule.get("field") or "title",
            "pattern": rule.get("pattern") or "",
            "action": rule.get("action") or "read",
            "value": rule.get("value") or "",
            "feed_id": rule.get("feed_id") or None,
            # Asking what a disabled rule would do is the point of asking.
            "enabled": 1,
            "is_regex": int(bool(rule.get("is_regex", True))),
        }
        if draft["field"] not in RULE_FIELDS or draft["action"] not in RULE_ACTIONS:
            raise ValueError("Invalid rule field or action.")
        compiled = compile_rule(draft["pattern"], draft["is_regex"])
        if draft["action"] == "rewrite":
            problem = replacement_error(compiled[0], draft["value"])
            if problem:
                raise ValueError(problem)
        return self.scan_rules([(draft, compiled)], write=False)

    def import_opml(self, xml):
        root = fromstring(xml)
        if root.tag.lower() != "opml":
            raise ValueError("Expected an OPML document.")
        count = 0

        # Validate every URL before opening the write transaction (DNS can be
        # slow, so resolve concurrently). Folder/name/depth errors below still
        # roll back the whole import. A host that no longer resolves is kept:
        # its refresh job records the failure on the feed instead of losing a
        # large export to one dead blog.
        urls = {
            value for child in root.iter() for key, value in child.attrib.items() if key.lower() == "xmlurl"
        }
        unresolved = 0
        with ThreadPoolExecutor(max_workers=16) as pool:
            for future in [pool.submit(safe_url, url) for url in urls]:
                try:
                    future.result()
                except UnresolvedHost:
                    unresolved += 1

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
                        # Sibling names are unique: an outline reuses a same-named folder
                        # under the same parent rather than creating a twin.
                        matches = self.rows(
                            "SELECT id FROM folders WHERE name=? COLLATE NOCASE AND parent_id IS ?",
                            (name.strip(), parent),
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
        return {"imported": count, "unresolved": unresolved}

    def export_starred(self):
        return {
            "format": "feedelio-starred",
            "version": 1,
            "articles": self.rows(
                f"""SELECT a.*,f.title feed_title {ARTICLES}
               WHERE a.starred=1 AND a.deleted=0 AND f.deleted=0 ORDER BY a.published DESC"""
            ),
        }

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
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            data = {t: [dict(r) for r in self.db.execute(f"SELECT * FROM {t}")] for t in BACKUP_TABLES}
            media = {}
            for j in data["jobs"]:
                if j["kind"] == "download" and j["status"] == "done":
                    path = self.download_path(j["id"])
                    if not path.is_file():
                        raise ValueError(
                            f"Downloaded episode {j['id']} is missing; remove it before backing up."
                        )
                    media[j["id"]] = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"format": "feedelio", "version": 1, "created": now(), "tables": data, "media": media}

    def restore(self, backup):
        if backup.get("format") != "feedelio" or backup.get("version") != 1:
            raise ValueError("Unsupported backup format.")
        data = backup.get("tables", {})
        if set(data) != set(BACKUP_TABLES):
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
        if set(media) != expected_media or any(not is_uid(id) for id in media):
            raise ValueError("Backup has missing or invalid downloaded episodes.")
        decoded = {id: base64.b64decode(value, validate=True) for id, value in media.items()}
        written = []
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                # The extension creates its feed the moment it is pointed here; empty, it is not a library.
                if self.rows(
                    "SELECT id FROM feeds WHERE id!='chrome' OR EXISTS (SELECT 1 FROM articles WHERE feed_id='chrome') LIMIT 1"
                ):
                    raise ValueError("Restore requires an empty library. Use a new data directory.")
                if any((self.root / "downloads" / id).exists() for id in decoded):
                    raise ValueError("Restore requires an empty downloads directory.")
                self.db.execute("PRAGMA defer_foreign_keys=ON")
                for table in BACKUP_TABLES:
                    valid = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                    for row in data[table]:
                        if not set(row) <= valid:  # Older backups may lack newer columns.
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
                for id, content in decoded.items():
                    path = self.root / "downloads" / id
                    written.append(path)
                    path.write_bytes(content)
                if self.db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Backup contains invalid references.")
        except Exception:
            # Includes commit failures, not just file-write errors.
            for path in written:
                path.unlink(missing_ok=True)
            raise
        Core.frame_cache.pop(self.root, None)
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
            archive=self.archive_size(),
        )

    def archive_size(self):
        """Library size on disk: both databases (with WAL) and downloaded episodes."""
        files = [p for p in self.root.glob("*.sqlite*")] + list((self.root / "downloads").glob("*"))
        return dict(
            bytes=sum(p.stat().st_size for p in files if p.is_file()),
            downloads=sum(1 for p in (self.root / "downloads").iterdir() if p.is_file()),
        )

    def chrome_sync(self, entries):
        feed_id = READING_LIST
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO feeds(id,url,title,folder_id,created) VALUES('chrome','chrome:reading-list','Chrome Reading List','inbox',?)",
                (now(),),
            )
        result, remove = [], []
        for e in entries:
            url = e["url"]
            if urlsplit(url).scheme not in ("http", "https"):
                continue
            # Deleted here while Chrome still has it: Chrome removes it too.
            # (A page removed in Chrome loses its sync state, so adding it back
            # there brings it back here.)
            if self.rows(
                "SELECT 1 FROM articles a JOIN chrome_state c ON c.url=a.guid"
                " WHERE a.feed_id=? AND a.guid=? AND a.deleted=1",
                (feed_id, url),
            ):
                remove.append(url)
                continue
            # Preserve exact Chrome URLs for synchronization; cleaning only affects the article URL.
            # A page is dated when it was added to the list, so the view reads
            # newest first like any stream (and an import is not one moment).
            added = e.get("creationTime")
            published = datetime.fromtimestamp(added / 1000, timezone.utc).isoformat() if added else None
            id = self.ingest(feed_id, url, e.get("title") or url, url, published=published)
            if not id:
                continue
            # State sync needs a few columns, not the rendered article; the list
            # is resent every minute.
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                # Added back to Chrome after it was removed there.
                self.db.execute("UPDATE articles SET deleted=0 WHERE id=?", (id,))
                a = self.one(
                    "SELECT read,read_at,state_changed,body='' empty FROM articles WHERE id=?", (id,)
                )
                previous = self.rows("SELECT chrome_updated FROM chrome_state WHERE url=?", (url,))
                changed = not previous or e["lastUpdateTime"] > previous[0]["chrome_updated"]
                local_time = (
                    datetime.fromisoformat(a["state_changed"]).timestamp() * 1000 if a["state_changed"] else 0
                )
                if changed and e["lastUpdateTime"] > local_time:
                    stamp = datetime.fromtimestamp(e["lastUpdateTime"] / 1000, timezone.utc).isoformat()
                    self.db.execute(
                        "UPDATE articles SET read=?,read_at=?,state_changed=? WHERE id=?",
                        (int(e["hasBeenRead"]), stamp if e["hasBeenRead"] else a["read_at"], stamp, id),
                    )
                    a["read"] = e["hasBeenRead"]
                self.db.execute(
                    "INSERT OR REPLACE INTO chrome_state VALUES (?,?)", (url, e["lastUpdateTime"])
                )
                if a["empty"] and not self.rows(
                    "SELECT id FROM jobs WHERE kind='extract' AND json_extract(payload,'$.article_id')=?",
                    (id,),
                ):
                    self._enqueue("extract", {"article_id": id})
            result.append({"url": url, "hasBeenRead": bool(a["read"])})
        # The extension sends the whole list, so what is gone was removed in Chrome.
        kept = [e["url"] for e in entries]
        with self.db:
            gone = self.rows(
                "SELECT id,guid FROM articles WHERE feed_id=? AND guid NOT IN (SELECT value FROM json_each(?))",
                (feed_id, json.dumps(kept)),
            )
            for a in gone:
                self.db.execute("UPDATE articles SET deleted=1 WHERE id=?", (a["id"],))
                self.db.execute("DELETE FROM chrome_state WHERE url=?", (a["guid"],))
        return {"entries": result, "remove": remove}

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
            "control_backfill",
            "import_opml",
            "restore",
            "chrome_sync",
            "delete_download",
        }
        if action not in actions:
            raise ValueError("Unknown action.")
        return getattr(self, action)(**payload)
