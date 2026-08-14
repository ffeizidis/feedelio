"""The core service is the contract the API and worker are written against."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from feedelio.config import Settings
from feedelio.core import (
    UNFILED,
    Core,
    FeedExistsError,
    FeedNotFoundError,
    FeedUnavailableError,
    FolderExistsError,
    FolderNotFoundError,
    InvalidFolderNameError,
    OpmlError,
    make_core,
)
from feedelio.core.service import FOLDER_PREFIX, MAX_OPML_DEPTH
from feedelio.core.storage import PLUGINS, open_reader
from tests.conftest import FEED_FORMATS, FIXTURES, INOREADER_OPML, SAMPLE_FEED


def folder_tags(core: Core, url: str) -> list[str]:
    """The raw folder tags on a feed — how "exactly one folder" is stored."""
    return [key for key in core.reader.get_tag_keys(url) if key.startswith(FOLDER_PREFIX)]


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


def test_an_empty_library_has_no_folders(core: Core) -> None:
    assert core.list_folders() == []


def test_a_new_folder_is_empty_and_survives_having_no_feeds(core: Core) -> None:
    """A folder is a tag on the reader itself, so creating one is not a no-op."""
    folder = core.create_folder("  News  ")

    assert folder.name == "News"
    assert folder.feeds == []
    assert core.list_folders() == [folder]


def test_folders_are_listed_alphabetically_ignoring_case(core: Core) -> None:
    for name in ("zeta", "Alpha", "beta"):
        core.create_folder(name)

    assert [folder.name for folder in core.list_folders()] == ["Alpha", "beta", "zeta"]


def test_feeds_in_no_folder_are_reachable_as_the_unfiled_group(all_formats_core: Core) -> None:
    all_formats_core.create_folder("News")
    all_formats_core.move_feed(SAMPLE_FEED, "News")

    folders = all_formats_core.list_folders()

    # The pseudo-folder sorts last, so real folders keep the top of the sidebar.
    assert [folder.name for folder in folders] == ["News", UNFILED]
    assert [feed.url for feed in folders[0].feeds] == [SAMPLE_FEED]
    assert [feed.url for feed in folders[1].feeds] == ["sample.rdf", "sample.rss"]


def test_feeds_within_a_folder_are_sorted_by_title(all_formats_core: Core) -> None:
    all_formats_core.create_folder("News")
    for url in FEED_FORMATS:
        all_formats_core.move_feed(url, "News")

    titles = [feed.title for feed in all_formats_core.list_folders()[0].feeds]
    assert titles == sorted(title or "" for title in titles)


def test_a_feed_lives_in_exactly_one_folder(loaded_core: Core) -> None:
    """#16's first acceptance criterion: moving does not copy."""
    loaded_core.create_folder("News")
    loaded_core.create_folder("Tech")

    loaded_core.move_feed(SAMPLE_FEED, "News")
    loaded_core.move_feed(SAMPLE_FEED, "Tech")

    assert folder_tags(loaded_core, SAMPLE_FEED) == ["folder:Tech"]
    assert [f.name for f in loaded_core.list_folders() if f.feeds] == ["Tech"]


def test_only_the_empty_string_unfiles_a_feed(loaded_core: Core) -> None:
    """A blank-but-not-empty name must not quietly move a feed out of its folder."""
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "News")

    with pytest.raises(InvalidFolderNameError):
        loaded_core.move_feed(SAMPLE_FEED, "   ")

    assert folder_tags(loaded_core, SAMPLE_FEED) == ["folder:News"]

    loaded_core.move_feed(SAMPLE_FEED, UNFILED)
    assert folder_tags(loaded_core, SAMPLE_FEED) == []


def test_moving_a_feed_where_it_already_is_leaves_it_there(loaded_core: Core) -> None:
    """The move is idempotent, so a client can replay it without losing the feed."""
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "News")
    loaded_core.move_feed(SAMPLE_FEED, "News")

    assert folder_tags(loaded_core, SAMPLE_FEED) == ["folder:News"]


def test_moving_a_feed_out_of_every_folder_unfiles_it(loaded_core: Core) -> None:
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "News")

    loaded_core.move_feed(SAMPLE_FEED, UNFILED)

    assert folder_tags(loaded_core, SAMPLE_FEED) == []
    assert [(f.name, len(f.feeds)) for f in loaded_core.list_folders()] == [("News", 0), ("", 1)]


def test_a_folder_reads_as_one_merged_stream(all_formats_core: Core) -> None:
    """#16's second acceptance criterion."""
    all_formats_core.create_folder("News")
    all_formats_core.move_feed("sample.rss", "News")
    all_formats_core.move_feed("sample.rdf", "News")

    entries = all_formats_core.list_entries(folder="News")

    assert len(entries) == 4
    assert {entry.feed_url for entry in entries} == {"sample.rss", "sample.rdf"}
    dates: list[datetime] = []
    for entry in entries:
        date = entry.published or entry.updated
        assert date is not None, f"{entry.title} has no date"
        dates.append(date)
    assert dates == sorted(dates, reverse=True)
    assert len(all_formats_core.list_entries(folder="News", limit=1)) == 1


def test_the_unfiled_group_reads_as_one_stream_too(all_formats_core: Core) -> None:
    all_formats_core.create_folder("News")
    all_formats_core.move_feed("sample.rss", "News")

    entries = all_formats_core.list_entries(folder=UNFILED)

    assert {entry.feed_url for entry in entries} == {SAMPLE_FEED, "sample.rdf"}
    assert len(all_formats_core.list_entries()) == 6


def test_everything_is_unfiled_when_there_are_no_folders(all_formats_core: Core) -> None:
    assert len(all_formats_core.list_entries(folder=UNFILED)) == 6


def test_folder_names_are_matched_case_insensitively(loaded_core: Core) -> None:
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "  news  ")

    assert folder_tags(loaded_core, SAMPLE_FEED) == ["folder:News"]
    assert len(loaded_core.list_entries(folder="NEWS")) == 2


def test_deleting_a_folder_keeps_its_feeds(loaded_core: Core) -> None:
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "News")

    loaded_core.delete_folder("news")

    assert [feed.url for feed in loaded_core.list_feeds()] == [SAMPLE_FEED]
    assert loaded_core.status().entries == 2
    assert folder_tags(loaded_core, SAMPLE_FEED) == []
    assert [(f.name, len(f.feeds)) for f in loaded_core.list_folders()] == [(UNFILED, 1)]


def test_renaming_a_folder_takes_its_feeds_along(loaded_core: Core) -> None:
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "News")

    folder = loaded_core.rename_folder("News", " Headlines ")

    assert folder.name == "Headlines"
    assert [feed.url for feed in folder.feeds] == [SAMPLE_FEED]
    assert folder_tags(loaded_core, SAMPLE_FEED) == ["folder:Headlines"]
    assert len(loaded_core.list_entries(folder="Headlines")) == 2
    with pytest.raises(FolderNotFoundError):
        loaded_core.list_entries(folder="News")


def test_renaming_a_folder_to_itself_keeps_its_feeds(loaded_core: Core) -> None:
    """The rename is a retag; retagging with the same key must not drop it."""
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "News")

    folder = loaded_core.rename_folder("News", " News ")

    assert [feed.url for feed in folder.feeds] == [SAMPLE_FEED]


def test_a_rename_may_change_only_the_case(core: Core) -> None:
    core.create_folder("news")

    assert core.rename_folder("news", "News").name == "News"
    assert [folder.name for folder in core.list_folders()] == ["News"]


@pytest.mark.parametrize(
    "name",
    ["", "   ", "Tech/Rust", "x" * 65, "bad\nname"],
    ids=["empty", "blank", "slash", "too-long", "control-character"],
)
def test_unusable_folder_names_are_rejected(core: Core, name: str) -> None:
    """``/`` is turned away now so that nested folders (M2) can claim it."""
    with pytest.raises(InvalidFolderNameError):
        core.create_folder(name)

    core.create_folder("News")
    with pytest.raises(InvalidFolderNameError):
        core.rename_folder("News", name)

    assert [folder.name for folder in core.list_folders()] == ["News"]


def test_folder_names_are_unique_ignoring_case(core: Core) -> None:
    core.create_folder("News")
    core.create_folder("Tech")

    with pytest.raises(FolderExistsError):
        core.create_folder("  news ")
    with pytest.raises(FolderExistsError):
        core.rename_folder("Tech", "NEWS")

    assert [folder.name for folder in core.list_folders()] == ["News", "Tech"]


def test_operations_on_an_unknown_folder_are_errors(loaded_core: Core) -> None:
    with pytest.raises(FolderNotFoundError):
        loaded_core.delete_folder("Nope")
    with pytest.raises(FolderNotFoundError):
        loaded_core.rename_folder("Nope", "News")
    with pytest.raises(FolderNotFoundError):
        loaded_core.move_feed(SAMPLE_FEED, "Nope")
    with pytest.raises(FolderNotFoundError):
        loaded_core.list_entries(folder="Nope")


def test_moving_a_feed_we_do_not_have_is_an_error(core: Core) -> None:
    with pytest.raises(FeedNotFoundError):
        core.move_feed(SAMPLE_FEED, UNFILED)


def structure(core: Core) -> dict[str, list[str]]:
    """The sidebar tree as plain data: folder name -> the URLs in it."""
    return {
        folder.name: sorted(feed.url for feed in folder.feeds) for folder in core.list_folders()
    }


def opml_document(body: str) -> bytes:
    """A minimal subscription list wrapping ``body``, for the edge cases."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<opml version="1.0"><head><title>Test</title></head><body>{body}</body></opml>'
    ).encode()


def test_import_keeps_the_folders_of_an_inoreader_export(core: Core) -> None:
    """#15's acceptance criterion, one half of it.

    Categories become folders; a feed nested deeper than one category lands in
    the innermost one, since that is the label it actually carries and nested
    folders are M2; a feed with no category stays unfiled.
    """
    core.import_opml(INOREADER_OPML.read_bytes())

    assert structure(core) == {
        "News": ["https://example.com/atom.xml", "https://rss.example.com/feed.xml"],
        "Rust": ["https://this-week-in-rust.org/rss.xml"],
        "Saved for later": [],
        "Tech": ["https://rdf.example.com/feed.rdf"],
        UNFILED: ["https://blog.example.com/index.xml"],
    }
    # One folder per feed survives an import, nested categories included.
    assert all(len(folder_tags(core, feed.url)) <= 1 for feed in core.list_feeds())


def test_import_reports_what_it_did(core: Core) -> None:
    summary = core.import_opml(INOREADER_OPML.read_bytes())

    assert summary.added == 5
    assert summary.already_present == 0
    assert summary.failed == []
    # Document order, so the report reads like the file the user just uploaded.
    assert summary.folders_created == ["News", "Tech", "Rust", "Saved for later"]
    assert summary.folders_skipped == []


def test_import_does_not_fetch_the_feeds(core: Core) -> None:
    """Hundreds of feeds cannot be fetched inside one request; the worker will."""
    core.import_opml(INOREADER_OPML.read_bytes())

    status = core.status()
    assert status == type(status)(feeds=5, entries=0, unread=0, broken_feeds=0)
    assert all(feed.updated is None and not feed.broken for feed in core.list_feeds())


def test_import_leaves_a_feed_it_already_has_where_it_is(loaded_core: Core) -> None:
    """An import adds subscriptions; it does not reorganise the ones you have."""
    loaded_core.create_folder("Mine")
    loaded_core.move_feed(SAMPLE_FEED, "Mine")

    summary = loaded_core.import_opml(
        opml_document(
            '<outline text="News" title="News">'
            f'<outline type="rss" text="Sample" xmlUrl="{SAMPLE_FEED}"/>'
            "</outline>"
        )
    )

    assert summary.added == 0
    assert summary.already_present == 1
    assert folder_tags(loaded_core, SAMPLE_FEED) == ["folder:Mine"]
    # The category still becomes a folder: the file says it is there.
    assert structure(loaded_core) == {"Mine": [SAMPLE_FEED], "News": []}


def test_import_reuses_a_folder_that_already_exists(core: Core) -> None:
    core.create_folder("news")

    summary = core.import_opml(INOREADER_OPML.read_bytes())

    assert "News" not in summary.folders_created
    assert structure(core)["news"] == [
        "https://example.com/atom.xml",
        "https://rss.example.com/feed.xml",
    ]


def test_import_reports_a_category_it_cannot_use_as_a_folder_name(core: Core) -> None:
    """``/`` is reserved for nested folder paths, so such a category is refused.

    Refused, and said so: the feed is still subscribed, unfiled, rather than the
    whole upload failing over one awkward name.
    """
    summary = core.import_opml(
        opml_document(
            '<outline text="Science / Nature" title="Science / Nature">'
            '<outline type="rss" text="Nature" xmlUrl="https://nature.example.com/feed"/>'
            "</outline>"
        )
    )

    assert summary.added == 1
    assert summary.folders_created == []
    assert summary.folders_skipped == ["Science / Nature"]
    assert structure(core) == {UNFILED: ["https://nature.example.com/feed"]}


def test_import_reports_a_feed_it_cannot_add(core: Core) -> None:
    summary = core.import_opml(
        opml_document(
            '<outline text="News" title="News">'
            '<outline type="rss" text="Bad" xmlUrl="/etc/passwd"/>'
            '<outline type="rss" text="Good" xmlUrl="https://good.example.com/feed"/>'
            "</outline>"
        )
    )

    assert summary.added == 1
    assert summary.failed == ["/etc/passwd"]
    assert structure(core) == {"News": ["https://good.example.com/feed"]}


@pytest.mark.parametrize(
    "content",
    [b"not xml at all", b"<rss version='2.0'><channel/></rss>", b""],
    ids=["not-xml", "not-opml", "empty"],
)
def test_import_rejects_a_file_that_is_not_a_subscription_list(core: Core, content: bytes) -> None:
    with pytest.raises(OpmlError):
        core.import_opml(content)

    assert core.list_feeds() == []


def test_import_refuses_an_entity_bomb_before_reader_sees_it(core: Core) -> None:
    """The upload is parsed by defusedxml first; stdlib XML would expand this."""
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE opml ['
        b'<!ENTITY a "boom"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
        b"]><opml><body><outline>&b;</outline></body></opml>"
    )

    with pytest.raises(OpmlError):
        core.import_opml(bomb)


def test_import_refuses_a_list_nested_deeper_than_we_walk(core: Core) -> None:
    """A depth limit is what keeps a hostile file from recursing us to death."""
    body = "<outline text='deep'>" * (MAX_OPML_DEPTH + 1) + "</outline>" * (MAX_OPML_DEPTH + 1)

    with pytest.raises(OpmlError):
        core.import_opml(opml_document(body))


def test_an_outline_without_a_name_is_not_a_category(core: Core) -> None:
    """Only a named outline groups anything; a blank one passes its feeds through."""
    core.import_opml(
        opml_document(
            '<outline><outline type="rss" xmlUrl="https://a.example.com/feed"/></outline>'
        )
    )

    assert structure(core) == {UNFILED: ["https://a.example.com/feed"]}


def test_export_round_trips_the_folders_through_import(
    core: Core, settings: Settings, tmp_path: Path
) -> None:
    """#15's acceptance criterion, the other half — both directions, one test."""
    core.import_opml(INOREADER_OPML.read_bytes())
    exported = core.export_opml()

    with make_core(settings, db_path=tmp_path / "round-trip.sqlite") as second:
        second.import_opml(exported.content)

        assert structure(second) == structure(core)
        assert structure(second)["Rust"] == ["https://this-week-in-rust.org/rss.xml"]
        assert structure(second)["Saved for later"] == []
        assert structure(second)[UNFILED] == ["https://blog.example.com/index.xml"]


def test_export_carries_what_another_reader_needs(loaded_core: Core) -> None:
    """reader writes the outlines, so a fetched feed exports with its metadata."""
    loaded_core.create_folder("News")
    loaded_core.move_feed(SAMPLE_FEED, "News")

    content = loaded_core.export_opml().content.decode()

    assert '<outline text="News" title="News">' in content
    assert 'title="Feedelio Test Feed"' in content
    assert f'xmlUrl="{SAMPLE_FEED}"' in content
    assert 'htmlUrl="https://example.com/"' in content


def test_export_is_named_for_the_browser_to_save(core: Core) -> None:
    exported = core.export_opml()

    assert exported.filename.startswith("feedelio-subscriptions-")
    assert exported.filename.endswith(".opml")


def test_export_of_an_empty_library_is_still_a_subscription_list(core: Core) -> None:
    exported = core.export_opml()

    assert exported.content.startswith(b"<?xml")
    assert core.import_opml(exported.content).added == 0
