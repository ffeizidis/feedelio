"""HTTP surface: health, and serving the built SPA when one is present."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from feedelio import __version__
from feedelio.api import create_app
from feedelio.config import Settings
from feedelio.core import Core
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
