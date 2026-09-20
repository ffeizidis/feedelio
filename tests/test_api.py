from fastapi.testclient import TestClient

from feedelio.api import app


def test_api_state_and_exports(core, site):
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        result = client.post("/api/actions/subscribe", json={"payload": {"url": site[0] + "/rss"}})
        assert result.status_code == 200, result.text
        core.refresh(result.json()["id"])
        assert client.get("/api/overview").json()["counts"]["unread"] == 0  # New feeds start read.
        articles = client.get("/api/articles?q=independent").json()
        assert articles["total"] == 1
        id = articles["items"][0]["id"]
        assert client.get("/api/articles/" + id).status_code == 200
        assert (
            client.post(
                "/api/actions/change_articles", json={"payload": {"ids": [id], "read": False}}
            ).status_code
            == 200
        )
        assert client.get("/api/articles?unread=true").json()["total"] == 1
        assert client.get("/api/export/opml").headers["content-type"].startswith("application/xml")
        assert client.get("/api/export/json").json()["format"] == "feedelio"
        assert client.get("/api/articles/" + id + "/obsidian").status_code == 200


def test_local_extension_and_rebinding_guard(core):
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert (
            client.post(
                "/api/actions/chrome_sync",
                json={"payload": {"entries": []}},
                headers={"Origin": "chrome-extension://test-id"},
            ).status_code
            == 200
        )
        assert client.get("/api/overview", headers={"Host": "untrusted.example"}).status_code == 403


def test_explicit_no_login_docker_mode(core, monkeypatch):
    monkeypatch.setenv("FEEDELIO_NO_AUTH", "1")
    monkeypatch.setenv("FEEDELIO_TOKEN", "old-token-is-ignored")
    with TestClient(app, base_url="http://localhost:8000", client=("172.17.0.1", 40000)) as client:
        assert client.get("/api/overview").status_code == 200
        assert (
            client.post(
                "/api/actions/save_settings",
                json={"payload": {"values": {"theme": "dark"}}},
                headers={"Origin": "http://localhost:8000"},
            ).status_code
            == 200
        )
        assert not client.cookies
        assert client.post("/api/login", json={"payload": {}}).status_code == 200
        assert not client.cookies
        assert client.get("/api/overview", headers={"Host": "evil.example"}).status_code == 403
        assert (
            client.post(
                "/api/actions/mark_all",
                json={"payload": {}},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )


def test_auth_csrf_and_command_allowlist(core, monkeypatch):
    monkeypatch.setenv("FEEDELIO_TOKEN", "private-test-token")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/overview").status_code == 401
        assert client.post("/api/login", json={"payload": {"token": "wrong"}}).status_code == 401
        assert client.post("/api/login", json={"payload": {"token": "private-test-token"}}).status_code == 200
        assert client.get("/api/overview").status_code == 200
        assert (
            client.post(
                "/api/actions/save_settings",
                json={"payload": {"values": {"theme": "dark"}}},
                headers={"Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        assert client.post("/api/actions/reader", json={"payload": {}}).status_code == 400
        assert client.post("/api/actions/subscribe", json={"payload": {}}).status_code == 422
        assert client.get("/api/overview").headers["X-Frame-Options"] == "DENY"
    with TestClient(app) as extension:
        assert (
            extension.get("/api/overview", headers={"Authorization": "Bearer private-test-token"}).status_code
            == 200
        )


def test_page_addresses_serve_the_app_and_files_still_404(core):
    import pytest

    if not (app.routes and any(getattr(r, "name", "") == "web" for r in app.routes)):
        pytest.skip("Build the frontend with npm run build --prefix web first.")
    with TestClient(app) as client:
        home = client.get("/").text
        assert client.get("/f/abc123").text == home and client.get("/a/abc123").text == home
        assert client.get("/starred").text == home
        assert client.get("/missing.js").status_code == 404
