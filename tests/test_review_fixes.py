"""Deterministic regressions for the independent whole-codebase review."""

import base64
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from feedelio.api import app
from feedelio.core import Core
from feedelio.core.content import extract, fetch


def item(core, site, **kwargs):
    feed = core.subscribe(site[0] + "/rss")["id"]
    return feed, core.ingest(feed, "one", "Original article", site[0] + "/article", **kwargs)


def test_extraction_preserves_edits_made_during_fetch(core, site, monkeypatch):
    _, article = item(core, site)

    def delayed_extract(*args):
        with Core(core.root) as ui:
            ui.change_articles([article], read=True, starred=True, deleted=True, tags=["mine"])
        return dict(body="<p>Full text</p>", url=site[0] + "/canonical", transcript="", paywall=True)

    monkeypatch.setattr("feedelio.core.extract", delayed_extract)
    core.extract_article(article)
    saved = core.article(article)
    assert saved["read"] and saved["starred"] and saved["deleted"]
    assert saved["tags"] == ["mine", "paywall"]
    assert "Full text" in saved["body"]


def test_refresh_preserves_rename_and_interval_edits(core, site, monkeypatch):
    feed, _ = item(core, site)
    original = core.reader

    @contextmanager
    def reader(*args, **kwargs):
        with original(*args, **kwargs) as reader:
            update = reader.update_feed

            def delayed_update(*args, **kwargs):
                result = update(*args, **kwargs)
                with Core(core.root) as ui:
                    ui.edit_feed(feed, title="User title", options={"interval": 17})
                return result

            reader.update_feed = delayed_update
            yield reader

    monkeypatch.setattr(core, "reader", reader)
    core.refresh(feed)
    saved = core.feeds()[0]
    assert saved["title"] == "User title" and saved["interval"] == 17
    remaining = (datetime.fromisoformat(saved["next_check"]) - datetime.now(timezone.utc)).total_seconds()
    assert 16 * 60 < remaining <= 17 * 60


@pytest.mark.parametrize("operation", ["ingest", "reapply", "purge"])
def test_state_sensitive_selection_holds_write_lock(core, site, monkeypatch, operation):
    feed, article = item(core, site)
    core.change_articles([article], read=True)
    with core.db:
        core.db.execute(
            "UPDATE articles SET added=?", ((datetime.now(timezone.utc) - timedelta(days=100)).isoformat(),)
        )
    rows = core.rows
    checked = []

    def check(sql, params=()):
        if "SELECT * FROM articles" in sql or "SELECT id,guid FROM articles" in sql:
            with sqlite3.connect(core.root / "app.sqlite", timeout=0) as other:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("UPDATE articles SET starred=1 WHERE id=?", (article,))
            checked.append(True)
        return rows(sql, params)

    monkeypatch.setattr(core, "rows", check)
    if operation == "ingest":
        core.ingest(feed, "one", "Publisher correction", site[0] + "/article")
    elif operation == "reapply":
        core.reapply_rules()
    else:
        core.purge()
    assert checked


def test_opposing_folder_moves_remain_a_tree(core):
    a, b = [core.save_folder(name)["id"] for name in ("A", "B")]
    barrier = threading.Barrier(2)

    def move(id, parent):
        with Core(core.root) as connection:
            barrier.wait(timeout=5)
            try:
                connection.save_folder(id, parent, id)
                return True
            except ValueError:
                return False

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(move, a, b), pool.submit(move, b, a)]
        assert sorted(f.result(timeout=10) for f in futures) == [False, True]
    assert len(core.descendants(a)) <= 2 and len(core.descendants(b)) <= 2


@pytest.mark.parametrize(
    "scope", [{"q": "target"}, {"tag": "target"}, {"view": "starred"}, {"view": "history"}]
)
def test_mark_stream_respects_filters_and_undo(core, site, scope):
    feed, unrelated = item(core, site)
    target = core.ingest(feed, "target", "Target", site[0] + "/target", tags=["target"])
    core.change_articles([target], read=True, opened=True, starred=True)
    core.change_articles([target], read=False)
    result = core.mark_all(**scope)
    assert result["count"] == 1
    assert core.article(target)["read"] and not core.article(unrelated)["read"]
    core.undo(result["undo"])
    assert not core.article(target)["read"] and not core.article(unrelated)["read"]


def test_toggling_rule_preserves_dependent_order(core, site):
    feed, _ = item(core, site)
    first = core.save_rule("Tag", "title", "match", "tag", "flag")["id"]
    second = core.save_rule("Read tagged", "tags", "flag", "read")["id"]
    for enabled in (False, True):
        core.save_rule("Tag", "title", "match", "tag", "flag", enabled=enabled, id=first)
    assert [r["id"] for r in core.rules()] == [first, second]
    article = core.ingest(feed, "matched", "match", site[0] + "/match")
    assert core.article(article)["read"]


def test_enclosures_update_without_body_change_and_keep_canonical(core, site):
    feed, article = item(core, site, enclosures=[{"href": site[0] + "/old.mp3", "type": "audio/mpeg"}])
    enclosures = [{"href": site[0] + "/new.mp3", "type": "audio/mpeg"}]
    core.ingest(feed, "one", "Original article", site[0] + "/changed", enclosures=enclosures)
    assert core.article(article)["enclosures"] == enclosures
    assert core.article(article)["url"] == site[0] + "/changed"
    with core.db:
        core.db.execute("UPDATE articles SET extracted=1,url=? WHERE id=?", (site[0] + "/canonical", article))
    core.ingest(feed, "one", "Original article", site[0] + "/changed-again", enclosures=[])
    assert core.article(article)["url"] == site[0] + "/canonical"
    assert core.article(article)["enclosures"] == []


def test_forged_local_host_cannot_authorize_remote_peer(core, monkeypatch):
    monkeypatch.delenv("FEEDELIO_TOKEN", raising=False)
    with TestClient(app, base_url="http://localhost", client=("198.51.100.10", 40000)) as remote:
        assert remote.get("/api/overview").status_code == 403
        assert remote.post("/api/actions/mark_all", json={"payload": {}}).status_code == 403
        assert remote.get("/api/overview", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 40000)) as local:
        assert local.get("/api/overview").status_code == 200


def test_fetch_cookies_stay_at_subscription_origin(core, site):
    base, routes, requests = site
    other = base.replace("127.0.0.1", "localhost")
    fetch(base + "/article", {"cookie": "secret=one", "cookie_origin": base})
    assert requests[-1][1]["Cookie"] == "secret=one"
    fetch(other + "/article", {"cookie": "secret=one", "cookie_origin": base})
    assert "Cookie" not in requests[-1][1]
    fetch(base + "/article", {"cookie": "secret=one", "cookie_origin": base.replace("http:", "https:")})
    assert "Cookie" not in requests[-1][1]


def test_feed_transcript_and_migration_do_not_leak_cookies(core, site):
    base, routes, requests = site
    other = base.replace("127.0.0.1", "localhost")
    routes["/transcript"] = (200, {}, "Provided transcript")
    routes["/with-track"] = (
        200,
        {"Content-Type": "application/rss+xml"},
        f'''<rss version="2.0"><channel><title>Feed</title><description>Test</description><item><guid>track</guid><title>Track</title><enclosure url="{other}/transcript" type="text/plain"/></item></channel></rss>''',
    )
    feed = core.subscribe(base + "/with-track", options={"cookie": "secret=one"})["id"]
    core.refresh(feed)
    assert "Cookie" not in next(headers for path, headers in requests if path == "/transcript")
    routes["/moved"] = (301, {"Location": other + "/rss"}, "")
    moved = core.subscribe(base + "/moved", options={"cookie": "secret=two"})["id"]
    core.refresh(moved)
    core.refresh(moved)
    assert "Cookie" not in [headers for path, headers in requests if path == "/rss"][-1]
    assert not next(f for f in core.feeds() if f["id"] == moved)["options"].get("cookie")


def test_canonical_does_not_change_resource_base_and_bad_track_is_optional(core, site):
    base, routes, requests = site
    routes["/amp/story"] = (
        200,
        {},
        '<link rel="canonical" href="/story"><article><p>Usable body</p><img src="photo.jpg"><a href="related">Related</a></article><track kind="captions" src="captions.vtt">',
    )
    routes["/amp/captions.vtt"] = (200, {}, "Provided captions")
    result = extract(base + "/amp/story", {"selector": "article"})
    assert result["url"] == base + "/story"
    assert base + "/amp/photo.jpg" in result["body"]
    assert base + "/amp/related" in result["body"]
    assert result["transcript"] == "Provided captions"
    routes["/amp/captions.vtt"] = (404, {}, "Missing")
    result = extract(base + "/amp/story", {"selector": "article"})
    assert "Usable body" in result["body"] and result["transcript"] == ""


def test_plain_text_backfill_preserves_literal_html(core, site):
    base, routes, _ = site
    routes["/archive"] = (
        200,
        {"Content-Type": "application/feed+json"},
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "Archive",
                "items": [{"id": "archive", "content_text": "Use <widget> here & enjoy."}],
            }
        ),
    )
    feed = core.subscribe(base + "/rss")["id"]
    core.backfill(feed, base + "/archive")
    article = core.article(core.articles()["items"][0]["id"])
    assert article["text"] == "Use <widget> here & enjoy."


def test_restore_checks_empty_library_under_write_lock(core, site, monkeypatch):
    with Core(core.root / "source") as source:
        source.subscribe(site[0] + "/rss")
        backup = source.backup()
    rows = core.rows
    checked = []

    def check(sql, params=()):
        if sql == "SELECT id FROM feeds LIMIT 1":
            with sqlite3.connect(core.root / "app.sqlite", timeout=0) as other:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("INSERT INTO settings VALUES('concurrent','1')")
            checked.append(True)
        return rows(sql, params)

    monkeypatch.setattr(core, "rows", check)
    core.restore(backup)
    assert checked


def test_restore_commit_failure_cleans_new_media(core, site):
    with Core(core.root / "source") as source:
        source.subscribe(site[0] + "/rss")
        job = source.enqueue("download", {"article_id": "test", "url": site[0] + "/audio"})["id"]
        with source.db:
            source.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job,))
        (source.root / "downloads" / job).write_bytes(b"audio")
        backup = source.backup()
    connection = core.db

    class FailCommit:
        def __getattr__(self, name):
            return getattr(connection, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            connection.rollback()
            raise sqlite3.OperationalError("simulated commit failure")

    core.db = FailCommit()
    try:
        with pytest.raises(sqlite3.OperationalError, match="simulated"):
            core.restore(backup)
    finally:
        core.db = connection
    assert core.feeds() == [] and list((core.root / "downloads").iterdir()) == []
    assert backup["media"][job] == base64.b64encode(b"audio").decode()


@pytest.mark.parametrize("operation", ["backup", "delete"])
def test_media_export_and_deletion_hold_same_write_lock(core, site, monkeypatch, operation):
    _, article = item(core, site)
    job = core.enqueue("download", {"article_id": article, "url": site[0] + "/audio"})["id"]
    with core.db:
        core.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job,))
    path = core.root / "downloads" / job
    path.write_bytes(b"audio")
    original = core.download_path
    checked = []

    def check(id):
        with sqlite3.connect(core.root / "app.sqlite", timeout=0) as other:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE jobs SET status='removed' WHERE id=?", (id,))
        checked.append(True)
        return original(id)

    monkeypatch.setattr(core, "download_path", check)
    if operation == "backup":
        assert core.backup()["media"][job] == base64.b64encode(b"audio").decode()
    else:
        core.delete_download(job)
        assert not path.exists()
    assert checked


@pytest.mark.parametrize("operation", ["extract", "backfill"])
def test_secondary_requests_do_not_receive_feed_cookie(core, site, operation):
    base, _, requests = site
    other = base.replace("127.0.0.1", "localhost")
    feed = core.subscribe(base + "/rss", options={"cookie": "private=secret", "selector": "article"})["id"]
    if operation == "extract":
        article = core.ingest(feed, "external", "External article", other + "/article")
        core.extract_article(article)
    else:
        core.backfill(feed, other + "/json")
    assert requests
    assert all("Cookie" not in headers for _, headers in requests)


@pytest.mark.parametrize("metadata", ['"captionTracks":not-json', '"captionTracks":[null]'])
def test_malformed_caption_metadata_does_not_discard_body(monkeypatch, metadata):
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    html = "<article><p>Usable body</p></article><script>" + metadata + "</script>"
    monkeypatch.setattr("feedelio.core.content.fetch", lambda *a: (html.encode(), url, {}))
    result = extract(url, {"selector": "article"})
    assert "Usable body" in result["body"] and result["transcript"] == ""
