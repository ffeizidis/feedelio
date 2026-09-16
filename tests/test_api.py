from fastapi.testclient import TestClient

from feedelio.api import app


def test_api_state_and_exports(core, site):
    with TestClient(app, base_url="http://localhost") as client:
        result = client.post("/api/actions/subscribe", json={"payload": {"url": site[0] + "/rss"}})
        assert result.status_code == 200, result.text
        core.refresh(result.json()["id"])
        assert client.get("/api/overview").json()["counts"]["unread"] == 1
        articles = client.get("/api/articles?q=independent").json()
        assert articles["total"] == 1
        id = articles["items"][0]["id"]
        assert client.get("/api/articles/" + id).status_code == 200
        assert (
            client.post(
                "/api/actions/change_articles", json={"payload": {"ids": [id], "read": True}}
            ).status_code
            == 200
        )
        assert client.get("/api/articles?unread=true").json()["total"] == 0
        assert client.get("/api/export/opml").headers["content-type"].startswith("application/xml")
        assert client.get("/api/export/json").json()["format"] == "feedelio"
        assert client.get("/api/articles/" + id + "/obsidian").status_code == 200


def test_local_extension_and_rebinding_guard(core):
    with TestClient(app, base_url="http://localhost") as client:
        assert (
            client.post(
                "/api/actions/chrome_sync",
                json={"payload": {"entries": []}},
                headers={"Origin": "chrome-extension://test-id"},
            ).status_code
            == 200
        )
        assert client.get("/api/overview", headers={"Host": "untrusted.example"}).status_code == 403


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
