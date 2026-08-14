"""The service layer the rest of the app is written against."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from reader import Reader

from feedelio.config import Settings
from feedelio.core.storage import open_reader


@dataclass(frozen=True, slots=True)
class Status:
    """A snapshot of the library, cheap enough to poll."""

    feeds: int
    entries: int
    unread: int
    broken_feeds: int


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
