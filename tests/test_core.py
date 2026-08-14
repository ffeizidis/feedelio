"""The core service is the contract the API and worker are written against."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from feedelio.config import Settings
from feedelio.core import Core, FeedExistsError, FeedUnavailableError, make_core
from feedelio.core.storage import PLUGINS, open_reader
from tests.conftest import FEED_FORMATS, FIXTURES, SAMPLE_FEED


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


def test_subscribe_fetches_the_feed_immediately(core: Core) -> None:
    feed = core.subscribe(SAMPLE_FEED)

    assert feed.url == SAMPLE_FEED
    assert feed.title == "Feedelio Test Feed"
    assert feed.link == "https://example.com/"
    assert feed.version == "atom10"
    assert not feed.broken
    assert core.status().entries == 2


@pytest.mark.parametrize(("url", "version"), FEED_FORMATS.items())
def test_subscribe_parses_every_supported_format(core: Core, url: str, version: str) -> None:
    """The acceptance criterion for #10: RSS 2.0, Atom and RSS 1.0 all land."""
    feed = core.subscribe(url)
    entries = core.list_entries(feed=url)

    assert feed.version == version
    assert len(entries) == 2
    assert all(entry.title and entry.link and entry.content for entry in entries)


def test_subscribing_twice_is_an_error(core: Core) -> None:
    core.subscribe(SAMPLE_FEED)
    with pytest.raises(FeedExistsError):
        core.subscribe(SAMPLE_FEED)


def test_subscribing_to_an_unfetchable_feed_leaves_no_subscription(core: Core) -> None:
    with pytest.raises(FeedUnavailableError):
        core.subscribe("does-not-exist.atom")

    assert core.list_feeds() == []


def test_a_rejected_subscription_does_not_leak_server_paths(core: Core) -> None:
    with pytest.raises(FeedUnavailableError) as caught:
        core.subscribe("does-not-exist.atom")

    assert str(FIXTURES) not in str(caught.value)


def test_subscribing_to_an_invalid_url_is_an_error(core: Core) -> None:
    with pytest.raises(FeedUnavailableError):
        core.subscribe("../conftest.py")

    assert core.list_feeds() == []


def test_list_feeds_returns_every_subscription(all_formats_core: Core) -> None:
    feeds = all_formats_core.list_feeds()

    assert {feed.url for feed in feeds} == set(FEED_FORMATS)
    assert {feed.version for feed in feeds} == set(FEED_FORMATS.values())
    assert [feed.title for feed in feeds] == sorted(feed.title or "" for feed in feeds)


def test_list_entries_exposes_what_the_reading_pane_needs(core: Core) -> None:
    core.subscribe("sample.rss")

    first = core.list_entries(feed="sample.rss")[-1]
    assert first.id == "urn:feedelio:rss-feed:1"
    assert first.feed_url == "sample.rss"
    assert first.title == "RSS first post"
    assert first.link == "https://rss.example.com/first"
    assert first.author == "Rita Rossi"
    assert first.published == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert first.content == "<p>Hello from the first RSS post.</p>"
    assert first.read is False
    assert first.important is False


def test_list_entries_is_newest_first_across_feeds(all_formats_core: Core) -> None:
    entries = all_formats_core.list_entries()

    assert len(entries) == 6

    dates: list[datetime] = []
    for entry in entries:
        date = entry.published or entry.updated
        assert date is not None, f"{entry.title} has no date"
        dates.append(date)
    assert dates == sorted(dates, reverse=True)


def test_list_entries_filters_and_limits(all_formats_core: Core) -> None:
    assert len(all_formats_core.list_entries(feed=SAMPLE_FEED)) == 2
    assert len(all_formats_core.list_entries(limit=3)) == 3

    entry = all_formats_core.list_entries(limit=1)[0]
    all_formats_core.reader.set_entry_read((entry.feed_url, entry.id), True)
    all_formats_core.reader.set_entry_important((entry.feed_url, entry.id), True)

    assert [e.id for e in all_formats_core.list_entries(read=True)] == [entry.id]
    assert len(all_formats_core.list_entries(read=False)) == 5
    assert [e.id for e in all_formats_core.list_entries(important=True)] == [entry.id]

    flagged = all_formats_core.list_entries(read=True)[0]
    assert flagged.read is True
    assert flagged.important is True
