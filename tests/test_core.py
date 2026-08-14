"""The core service is the contract the API and worker are written against."""

from __future__ import annotations

from pathlib import Path

from feedelio.config import Settings
from feedelio.core import Core, make_core
from feedelio.core.storage import PLUGINS, open_reader
from tests.conftest import SAMPLE_FEED


def test_open_reader_creates_the_database(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "feedelio.sqlite"
    with open_reader(db) as reader:
        assert db.is_file()
        assert reader.get_feed_counts().total == 0


def test_search_is_enabled(core: Core) -> None:
    assert core.reader.is_search_enabled()


def test_plugins_are_loaded() -> None:
    assert ".ua_fallback" in PLUGINS
    assert ".entry_dedupe" in PLUGINS


def test_updating_a_feed_stores_its_entries(loaded_core: Core) -> None:
    titles = {entry.title for entry in loaded_core.reader.get_entries()}
    assert titles == {"First post", "Second post"}


def test_status_counts_unread_and_broken(loaded_core: Core) -> None:
    status = loaded_core.status()
    assert status == type(status)(feeds=1, entries=2, unread=2, broken_feeds=0)

    first = next(iter(loaded_core.reader.get_entries()))
    loaded_core.reader.set_entry_read(first, True)
    assert loaded_core.status().unread == 1


def test_scheduled_update_skips_feeds_that_are_not_due(loaded_core: Core) -> None:
    loaded_core.update_feeds(scheduled=True)
    assert loaded_core.status().entries == 2


def test_make_core_can_override_the_database_path(tmp_path: Path) -> None:
    override = tmp_path / "override.sqlite"
    with make_core(Settings(db_path=tmp_path / "unused.sqlite"), db_path=override):
        assert override.is_file()
    assert not (tmp_path / "unused.sqlite").exists()


def test_broken_feeds_are_reported(core: Core) -> None:
    core.reader.add_feed("does-not-exist.atom", allow_invalid_url=True)
    core.update_feeds(scheduled=False)
    assert core.status().broken_feeds == 1
    assert SAMPLE_FEED not in {feed.url for feed in core.reader.get_feeds()}


def test_deliberately_broken_to_prove_ci_goes_red() -> None:
    assert 1 == 2
