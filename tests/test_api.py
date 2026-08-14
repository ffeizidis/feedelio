"""HTTP surface: health, and serving the built SPA when one is present."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from feedelio import __version__
from feedelio.api import create_app
from feedelio.config import Settings
from feedelio.core import Core


@pytest.fixture
def client(settings: Settings, loaded_core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(settings, core=loaded_core)) as test_client:
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
