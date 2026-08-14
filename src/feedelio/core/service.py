"""The service layer the rest of the app is written against."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType

from reader import Entry, Feed, Reader, exceptions

from feedelio.config import Settings
from feedelio.core.storage import open_reader


class FeedError(Exception):
    """A subscription could not be created."""


class FeedExistsError(FeedError):
    """The feed is already subscribed to."""


class FeedUnavailableError(FeedError):
    """The URL is not a feed we can fetch and parse."""


@dataclass(frozen=True, slots=True)
class Status:
    """A snapshot of the library, cheap enough to poll."""

    feeds: int
    entries: int
    unread: int
    broken_feeds: int


@dataclass(frozen=True, slots=True)
class FeedInfo:
    """A subscription, as the API and the sidebar see it."""

    url: str
    title: str | None
    link: str | None
    updated: datetime | None
    #: What feedparser made of it: ``rss20``, ``atom10``, ``rss10``, …
    version: str | None
    #: The last fetch failed; ``Feed.last_exception`` has the details.
    broken: bool


@dataclass(frozen=True, slots=True)
class EntryInfo:
    """An article. Identified by ``(feed_url, id)`` — ids are per-feed."""

    id: str
    feed_url: str
    feed_title: str | None
    title: str | None
    link: str | None
    author: str | None
    #: RSS gives us ``published``, Atom and RSS 1.0 usually only ``updated``;
    #: both are passed through so callers can fall back.
    published: datetime | None
    updated: datetime | None
    #: The entry body: content when the feed has it, otherwise the summary.
    content: str | None
    read: bool
    important: bool


def _feed_info(feed: Feed) -> FeedInfo:
    return FeedInfo(
        url=feed.url,
        title=feed.resolved_title,
        link=feed.link,
        updated=feed.updated,
        version=feed.version,
        broken=feed.last_exception is not None,
    )


def _entry_info(entry: Entry) -> EntryInfo:
    content = entry.get_content()
    return EntryInfo(
        id=entry.id,
        feed_url=entry.feed_url,
        feed_title=entry.feed_resolved_title,
        title=entry.title,
        link=entry.link,
        author=entry.authors_str,
        published=entry.published,
        updated=entry.updated,
        content=content.value if content else None,
        read=entry.read,
        important=bool(entry.important),
    )


class Core:
    """Feedelio's domain operations.

    Wraps a single :class:`reader.Reader`. reader is synchronous and its
    connections are thread-local, so callers on an event loop must run these
    methods in a worker thread (the API layer does).
    """

    def __init__(self, reader: Reader) -> None:
        self._reader = reader

    @property
    def reader(self) -> Reader:
        """Escape hatch for code that legitimately needs the reader API."""
        return self._reader

    def status(self) -> Status:
        """Counts used by the health endpoint and the sidebar totals."""
        feeds = self._reader.get_feed_counts()
        entries = self._reader.get_entry_counts()
        unread = self._reader.get_entry_counts(read=False)
        # reader reports counts as None when it cannot compute them.
        return Status(
            feeds=feeds.total or 0,
            entries=entries.total or 0,
            unread=unread.total or 0,
            broken_feeds=feeds.broken or 0,
        )

    def subscribe(self, url: str) -> FeedInfo:
        """Add a feed and fetch it now, so the caller gets entries straight away.

        A feed that cannot be fetched or parsed is not kept: a typo should not
        leave a permanently broken subscription behind.
        """
        try:
            self._reader.add_feed(url)
        except exceptions.FeedExistsError as exc:
            raise FeedExistsError(f"already subscribed to {url}") from exc
        except exceptions.InvalidFeedURLError as exc:
            raise FeedUnavailableError(str(exc)) from exc

        try:
            self._reader.update_feed(url)
        except exceptions.ParseError as exc:
            self._reader.delete_feed(url)
            raise FeedUnavailableError(str(exc)) from exc

        return _feed_info(self._reader.get_feed(url))

    def list_feeds(self) -> list[FeedInfo]:
        """Every subscription, by title."""
        return [_feed_info(feed) for feed in self._reader.get_feeds()]

    def list_entries(
        self,
        *,
        feed: str | None = None,
        read: bool | None = None,
        important: bool | None = None,
        limit: int | None = None,
    ) -> list[EntryInfo]:
        """Articles, newest first, optionally narrowed to one feed or flag."""
        entries = self._reader.get_entries(feed=feed, read=read, important=important, limit=limit)
        return [_entry_info(entry) for entry in entries]

    def update_feeds(self, *, scheduled: bool = True) -> None:
        """Fetch feeds that are due (or all of them when ``scheduled`` is off)."""
        self._reader.update_feeds(scheduled=scheduled, workers=4)

    def close(self) -> None:
        self._reader.close()

    def __enter__(self) -> Core:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def make_core(settings: Settings, *, db_path: Path | None = None) -> Core:
    """Build a :class:`Core` from settings, overriding the database path in tests."""
    return Core(open_reader(db_path or settings.db_path, feed_root=settings.feed_root))
