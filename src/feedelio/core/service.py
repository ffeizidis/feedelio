"""The service layer the rest of the app is written against."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType

from reader import Entry, Feed, FeedSort, Reader, exceptions

from feedelio.config import Settings
from feedelio.core.storage import open_reader

log = logging.getLogger(__name__)

#: Tag key prefix marking a folder. On a feed it means "this feed is in that
#: folder"; on the reader itself it means "that folder exists", which is what
#: keeps an empty folder in the sidebar. Nothing else writes ``folder:`` keys.
FOLDER_PREFIX = "folder:"

#: The pseudo-folder holding feeds that are in no folder. A real folder name is
#: never empty, so the empty string is free to mean "unfiled" — and the sidebar
#: can then treat every group it renders identically, including this one.
UNFILED = ""

#: Longest folder name we accept. A sidebar limit, not a storage one.
MAX_FOLDER_NAME = 64


class FeedError(Exception):
    """A subscription could not be created."""


class FeedExistsError(FeedError):
    """The feed is already subscribed to."""


class FeedNotFoundError(FeedError):
    """There is no such subscription."""


class FeedUnavailableError(FeedError):
    """The URL is not a feed we can fetch and parse."""


class FolderError(Exception):
    """A folder operation could not be carried out."""


class FolderExistsError(FolderError):
    """A folder by that name (ignoring case) is already there."""


class FolderNotFoundError(FolderError):
    """There is no such folder."""


class InvalidFolderNameError(FolderError):
    """The name is empty, too long, or uses a reserved character."""


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


@dataclass(frozen=True, slots=True)
class FolderInfo:
    """A folder and its feeds, in the order the sidebar renders them."""

    #: Empty for the unfiled pseudo-folder; see :data:`UNFILED`.
    name: str
    feeds: list[FeedInfo]


def _folder_key(name: str) -> str:
    return f"{FOLDER_PREFIX}{name}"


def _clean_name(name: str) -> str:
    """Trim a folder name and refuse the ones we cannot render or store."""
    cleaned = name.strip()
    if not cleaned:
        raise InvalidFolderNameError("a folder needs a name")
    if len(cleaned) > MAX_FOLDER_NAME:
        raise InvalidFolderNameError(f"folder names are at most {MAX_FOLDER_NAME} characters")
    if not cleaned.isprintable():
        raise InvalidFolderNameError("folder names cannot contain control characters")
    if "/" in cleaned:
        # Reserved so that nested folders (M2) can spell a path as "Tech/Rust"
        # without having to migrate the names people already typed.
        raise InvalidFolderNameError("'/' is reserved for nested folders")
    return cleaned


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
            raise FeedUnavailableError(f"not a usable feed URL: {url}") from exc

        try:
            self._reader.update_feed(url)
        except exceptions.ParseError as exc:
            self._reader.delete_feed(url)
            # reader's message can carry local paths and resolved URLs; the
            # caller gets a flat one and the detail goes to the log.
            log.info("subscription to %s failed", url, exc_info=exc)
            raise FeedUnavailableError(f"could not fetch or parse {url}") from exc

        return _feed_info(self._reader.get_feed(url))

    def list_feeds(self) -> list[FeedInfo]:
        """Every subscription, by title.

        ``sort`` is reader's default, passed explicitly so the ordering is part
        of this layer's contract rather than something a caller has to look up.
        """
        return [_feed_info(feed) for feed in self._reader.get_feeds(sort=FeedSort.TITLE)]

    def list_entries(
        self,
        *,
        feed: str | None = None,
        folder: str | None = None,
        read: bool | None = None,
        important: bool | None = None,
        limit: int | None = None,
    ) -> list[EntryInfo]:
        """Articles, newest first, optionally narrowed to one feed, folder or flag.

        ``folder`` is a folder name, or :data:`UNFILED` for the feeds in none;
        leaving it out means every feed. A folder's articles come back as the
        one merged stream #16 asks for, because reader does the merging.
        """
        entries = self._reader.get_entries(
            feed=feed,
            feed_tags=self._folder_filter(folder),
            read=read,
            important=important,
            limit=limit,
        )
        return [_entry_info(entry) for entry in entries]

    # -- Folders ----------------------------------------------------------
    #
    # Folders are a convention over reader's tags, not a table of their own:
    # a feed is in folder X when it carries the tag ``folder:X``, and X exists
    # when the reader itself carries the same key. One folder per feed is not
    # something the storage can express, so :meth:`move_feed` — the only writer
    # of those keys — enforces it.

    def list_folders(self) -> list[FolderInfo]:
        """Every folder with its feeds: the tree the sidebar renders.

        Folders come alphabetically, and the unfiled feeds last, as a group
        with an empty name — omitted when there are none.
        """
        names = self._folder_names()
        folders = [FolderInfo(name=name, feeds=self._feeds_in(name)) for name in names]
        unfiled = self._feeds_matching([f"-{_folder_key(name)}" for name in names])
        if unfiled:
            folders.append(FolderInfo(name=UNFILED, feeds=unfiled))
        return folders

    def create_folder(self, name: str) -> FolderInfo:
        """Add an empty folder, ready for feeds to be moved into."""
        cleaned = _clean_name(name)
        if (clash := self._find_folder(cleaned)) is not None:
            raise FolderExistsError(f"a folder named {clash!r} already exists")
        self._reader.set_tag((), _folder_key(cleaned))
        return FolderInfo(name=cleaned, feeds=[])

    def rename_folder(self, name: str, new_name: str) -> FolderInfo:
        """Rename a folder, taking its feeds with it."""
        current = self._resolve_folder(name)
        cleaned = _clean_name(new_name)
        clash = self._find_folder(cleaned)
        if clash is not None and clash != current:
            raise FolderExistsError(f"a folder named {clash!r} already exists")
        if cleaned != current:
            self._retag(_folder_key(current), _folder_key(cleaned))
        return FolderInfo(name=cleaned, feeds=self._feeds_in(cleaned))

    def delete_folder(self, name: str) -> None:
        """Drop a folder. Its feeds stay subscribed and become unfiled."""
        self._retag(_folder_key(self._resolve_folder(name)), None)

    def move_feed(self, url: str, folder: str) -> None:
        """Put a feed in ``folder``, or in none of them (:data:`UNFILED`).

        Whichever folder it was in, it is not in it afterwards: that is what
        makes "exactly one folder" true rather than merely intended.

        Only the empty string unfiles. A blank-but-not-empty name is a client
        bug, and unfiling a feed on the strength of one would be a silent
        change of state — it is rejected, exactly as it is when creating.
        """
        try:
            self._reader.get_feed(url)
        except exceptions.FeedNotFoundError as exc:
            raise FeedNotFoundError(f"not subscribed to {url}") from exc

        wanted = (
            None if folder == UNFILED else _folder_key(self._resolve_folder(_clean_name(folder)))
        )
        for key in list(self._reader.get_tag_keys(url)):
            if key.startswith(FOLDER_PREFIX) and key != wanted:
                self._reader.delete_tag(url, key)
        if wanted is not None:
            self._reader.set_tag(url, wanted)

    def _folder_names(self) -> list[str]:
        """The folders that exist, alphabetically, case-insensitively."""
        keys = self._reader.get_tag_keys(())
        names = [key.removeprefix(FOLDER_PREFIX) for key in keys if key.startswith(FOLDER_PREFIX)]
        return sorted(names, key=str.casefold)

    def _find_folder(self, name: str) -> str | None:
        """The stored spelling of ``name``, or ``None``. Names ignore case."""
        folded = name.strip().casefold()
        return next((n for n in self._folder_names() if n.casefold() == folded), None)

    def _resolve_folder(self, name: str) -> str:
        found = self._find_folder(name)
        if found is None:
            raise FolderNotFoundError(f"no folder named {name.strip()!r}")
        return found

    def _feeds_matching(self, tags: list[str]) -> list[FeedInfo]:
        return [_feed_info(f) for f in self._reader.get_feeds(tags=tags, sort=FeedSort.TITLE)]

    def _feeds_in(self, name: str) -> list[FeedInfo]:
        return self._feeds_matching([_folder_key(name)])

    def _folder_filter(self, folder: str | None) -> list[str] | None:
        """``folder`` as one of reader's feed-tag filters.

        Unfiled has no tag of its own, so it is "in none of the folders there
        are" — which is also why an empty library filters nothing away.
        """
        if folder is None:
            return None
        if folder.strip() == UNFILED:
            return [f"-{_folder_key(name)}" for name in self._folder_names()]
        return [_folder_key(self._resolve_folder(folder))]

    def _retag(self, key: str, new_key: str | None) -> None:
        """Move every feed tagged ``key`` onto ``new_key``, or untag them."""
        for feed in list(self._reader.get_feeds(tags=[key])):
            if new_key is not None:
                self._reader.set_tag(feed, new_key)
            self._reader.delete_tag(feed, key)
        if new_key is not None:
            self._reader.set_tag((), new_key)
        self._reader.delete_tag((), key)

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
