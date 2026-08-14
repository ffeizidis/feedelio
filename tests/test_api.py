"""HTTP surface: health, and serving the built SPA when one is present."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from feedelio import __version__
from feedelio.api import create_app
from feedelio.config import Settings
from feedelio.core import Core, FolderError, FolderInfo, FolderNotFoundError
from tests.conftest import FEED_FORMATS, SAMPLE_FEED


@pytest.fixture
def client(settings: Settings, loaded_core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(settings, core=loaded_core)) as test_client:
        yield test_client


@pytest.fixture
def library(settings: Settings, all_formats_core: Core) -> Iterator[TestClient]:
    """A client over a library holding one feed of each format."""
    with TestClient(create_app(settings, core=all_formats_core)) as test_client:
        yield test_client


def test_health_reports_library_counts(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "feeds": 1,
        "entries": 2,
        "unread": 2,
        "broken_feeds": 0,
    }


def test_no_spa_routes_without_a_build(client: TestClient) -> None:
    assert client.get("/").status_code == 404


def test_spa_is_served_when_built(settings: Settings, core: Core) -> None:
    assets = settings.static_dir / "assets"
    assets.mkdir(parents=True)
    (settings.static_dir / "index.html").write_text("<title>Feedelio</title>")
    (assets / "app.js").write_text("export default 1;")

    with TestClient(create_app(settings, core=core)) as client:
        assert "<title>Feedelio</title>" in client.get("/").text
        assert client.get("/assets/app.js").text == "export default 1;"
        # Client-side routes fall back to the entry point, API routes do not.
        assert "<title>Feedelio</title>" in client.get("/folder/news").text
        assert client.get("/api/health").json()["status"] == "ok"


def test_app_opens_its_own_core_when_not_given_one(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["feeds"] == 0
    assert settings.db_path.is_file()


def test_subscribing_stores_and_lists_a_feed(settings: Settings, core: Core) -> None:
    with TestClient(create_app(settings, core=core)) as client:
        response = client.post("/api/feeds", json={"url": SAMPLE_FEED})

        assert response.status_code == 201
        assert response.json() == {
            "url": SAMPLE_FEED,
            "title": "Feedelio Test Feed",
            "link": "https://example.com/",
            "updated": "2026-01-02T10:00:00Z",
            "version": "atom10",
            "broken": False,
        }
        assert client.get("/api/feeds").json() == [response.json()]


def test_every_format_round_trips_through_the_api(library: TestClient) -> None:
    """#10's acceptance criterion, over HTTP."""
    feeds = library.get("/api/feeds").json()
    assert {feed["url"]: feed["version"] for feed in feeds} == FEED_FORMATS

    for url, expected in FEED_FORMATS.items():
        entries = library.get("/api/entries", params={"feed": url}).json()
        assert len(entries) == 2, expected
        assert all(entry["feed_url"] == url and entry["content"] for entry in entries)


def test_entries_carry_the_fields_the_ui_reads(library: TestClient) -> None:
    entries = library.get("/api/entries", params={"feed": "sample.rss"}).json()

    assert entries[-1] == {
        "id": "urn:feedelio:rss-feed:1",
        "feed_url": "sample.rss",
        "feed_title": "Feedelio RSS 2.0 Feed",
        "title": "RSS first post",
        "link": "https://rss.example.com/first",
        "author": "Rita Rossi",
        "published": "2026-01-01T10:00:00Z",
        "updated": None,
        "content": "<p>Hello from the first RSS post.</p>",
        "read": False,
        "important": False,
    }


def test_entries_are_limited_and_filterable(library: TestClient) -> None:
    assert len(library.get("/api/entries").json()) == 6
    assert len(library.get("/api/entries", params={"limit": 2}).json()) == 2
    assert len(library.get("/api/entries", params={"read": False}).json()) == 6
    assert library.get("/api/entries", params={"read": True}).json() == []
    assert library.get("/api/entries", params={"important": True}).json() == []
    assert library.get("/api/entries", params={"limit": 0}).status_code == 422
    assert library.get("/api/entries", params={"limit": 10_000}).status_code == 422


def test_subscribing_twice_conflicts(library: TestClient) -> None:
    response = library.post("/api/feeds", json={"url": SAMPLE_FEED})

    assert response.status_code == 409
    assert SAMPLE_FEED in response.json()["detail"]


def test_subscribing_to_an_unfetchable_feed_is_rejected(settings: Settings, core: Core) -> None:
    with TestClient(create_app(settings, core=core)) as client:
        response = client.post("/api/feeds", json={"url": "does-not-exist.atom"})

        assert response.status_code == 400
        assert client.get("/api/feeds").json() == []


def test_the_sidebar_gets_folders_with_their_feeds(library: TestClient) -> None:
    assert library.post("/api/folders", json={"name": "News"}).json() == {
        "name": "News",
        "feeds": [],
    }
    moved = library.put("/api/feeds/folder", json={"url": SAMPLE_FEED, "folder": "News"})
    assert moved.status_code == 204

    folders = library.get("/api/folders").json()

    assert [folder["name"] for folder in folders] == ["News", ""]
    assert [feed["url"] for feed in folders[0]["feeds"]] == [SAMPLE_FEED]
    assert folders[0]["feeds"][0]["title"] == "Feedelio Test Feed"
    assert [feed["url"] for feed in folders[1]["feeds"]] == ["sample.rdf", "sample.rss"]


def test_a_folder_is_one_entry_stream_over_http(library: TestClient) -> None:
    library.post("/api/folders", json={"name": "News"})
    for url in ("sample.rss", "sample.rdf"):
        moved = library.put("/api/feeds/folder", json={"url": url, "folder": "News"})
        assert moved.status_code == 204

    entries = library.get("/api/entries", params={"folder": "News"}).json()
    unfiled = library.get("/api/entries", params={"folder": ""}).json()

    assert len(entries) == 4
    assert {entry["feed_url"] for entry in entries} == {"sample.rss", "sample.rdf"}
    assert {entry["feed_url"] for entry in unfiled} == {SAMPLE_FEED}
    assert library.get("/api/entries", params={"folder": "Nope"}).status_code == 404


def test_renaming_and_deleting_a_folder(library: TestClient) -> None:
    library.post("/api/folders", json={"name": "News"})
    library.put("/api/feeds/folder", json={"url": SAMPLE_FEED, "folder": "News"})

    renamed = library.patch("/api/folders/News", json={"name": "Head lines"})
    assert renamed.json()["name"] == "Head lines"

    assert library.delete("/api/folders/Head lines").status_code == 204
    # The feeds outlive the folder; they are simply unfiled.
    assert library.get("/api/folders").json() == [
        {"name": "", "feeds": library.get("/api/feeds").json()}
    ]
    assert len(library.get("/api/entries").json()) == 6


def test_bad_folder_requests_are_reported(library: TestClient) -> None:
    library.post("/api/folders", json={"name": "News"})

    assert library.post("/api/folders", json={"name": " news "}).status_code == 409
    assert library.post("/api/folders", json={"name": "  "}).status_code == 400
    assert library.patch("/api/folders/News", json={"name": "a/b"}).status_code == 400
    assert library.patch("/api/folders/Nope", json={"name": "Fine"}).status_code == 404
    assert library.delete("/api/folders/Nope").status_code == 404

    library.post("/api/folders", json={"name": "Tech"})
    assert library.patch("/api/folders/Tech", json={"name": "news"}).status_code == 409

    moved = library.put("/api/feeds/folder", json={"url": SAMPLE_FEED, "folder": "Nope"})
    assert moved.status_code == 404
    missing = library.put("/api/feeds/folder", json={"url": "nope.atom", "folder": "News"})
    assert missing.status_code == 404


class UnmappedFolderError(FolderError):
    """A core error the API layer has no status for."""


class NarrowerFolderNotFoundError(FolderNotFoundError):
    """A more specific flavour of an error the API layer does map."""


def _always_raise(error: type[Exception]) -> Callable[[Core], list[FolderInfo]]:
    """A ``Core.list_folders`` that fails, to reach the handlers from a route."""

    def list_folders(self: Core) -> list[FolderInfo]:
        raise error("nothing a client should read")

    return list_folders


def test_an_unmapped_core_error_is_a_deliberate_500(
    library: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Core, "list_folders", _always_raise(UnmappedFolderError))

    response = library.get("/api/folders")

    # A status nobody chose is a bug, not a client error, and the message stays in the log.
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}


def test_a_subclass_of_a_mapped_error_keeps_its_status(
    library: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Core, "list_folders", _always_raise(NarrowerFolderNotFoundError))

    response = library.get("/api/folders")

    assert response.status_code == 404
    assert response.json() == {"detail": "nothing a client should read"}
