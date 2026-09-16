import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from feedelio.core import Core
from feedelio.core.content import embed_url, safe_url, sanitize


def subscription(core, site, path="/rss"):
    base, _, _ = site
    id = core.subscribe(base + path)["id"]
    core.refresh(id)
    return id


@pytest.mark.parametrize(
    "path,title",
    [("/rss", "Test journal"), ("/json", "JSON journal"), ("/atom", "Atom journal"), ("/rdf", "RSS one")],
)
def test_formats_and_repeat_fetch(core, site, path, title):
    id = subscription(core, site, path)
    assert core.feeds()[0]["title"] == title
    assert core.articles()["total"] == 1
    core.refresh(id)
    assert core.articles()["total"] == 1
    assert core.feeds()[0]["health"] == "healthy"


def test_conditional_headers_redirect_and_custom_ua(core, site):
    base, routes, requests = site
    routes["/moved"] = (301, {"Location": "/rss"}, "")
    id = core.subscribe(base + "/moved", options={"user_agent": "Feedelio-Test/1", "cookie": "test=ok"})["id"]
    core.refresh(id)
    assert core.feeds()[0]["url"] == base + "/rss"
    core.refresh(id)
    core.refresh(id)
    rss_requests = [headers for path, headers in requests if path == "/rss"]
    assert rss_requests[-1]["If-None-Match"] == '"feed-v1"'
    assert rss_requests[-1]["User-Agent"] == "Feedelio-Test/1"
    assert rss_requests[-1]["Cookie"] == "test=ok"
    assert core.articles()["total"] == 1


def test_discovery_and_hash_fallback(core, site):
    base, routes, _ = site
    assert core.discover(base + "/home")[0]["url"] == base + "/rss"
    routes["/hash"] = (
        200,
        {"Content-Type": "application/rss+xml"},
        '<rss version="2.0"><channel><title>Hash</title><description>test</description><item><title>No GUID or link</title><description>A stable article</description></item></channel></rss>',
    )
    id = subscription(core, site, "/hash")
    core.refresh(id)
    assert core.articles()["total"] == 1
    core.ingest(id, "another-hash", "Another linkless entry", "", "Different body")
    assert core.articles()["total"] == 2


def test_folder_tree_move_counts_and_cycle(core, site):
    id = subscription(core, site)
    parent = core.save_folder("Technology")["id"]
    child = core.save_folder("Engineering", parent)["id"]
    core.edit_feed(id, folder_id=child)
    assert core.articles(folder_id=parent)["total"] == 1
    assert next(f for f in core.overview()["folders"] if f["id"] == parent)["unread"] == 1
    with pytest.raises(ValueError, match="ancestors"):
        core.save_folder("Technology", child, parent)
    with pytest.raises(ValueError):
        core.edit_feed(id, folder_id="missing")
    core.delete_folder(child)
    assert core.feeds()[0]["folder_id"] == "inbox"


def test_state_undo_history_and_deleted_feeds(core, site):
    id = subscription(core, site)
    a = core.articles()["items"][0]
    result = core.change_articles([a["id"]], read=True, opened=True)
    assert core.articles(unread=True)["total"] == 0
    assert core.articles(view="history")["total"] == 1
    assert core.statistics()["days"][0]["opens"] == 1
    core.undo(result["undo"])
    assert core.articles(unread=True)["total"] == 1
    result = core.mark_all()
    assert result["count"] == 1
    core.undo(result["undo"])
    core.change_articles([a["id"]], starred=True, tags=["keep"])
    assert core.articles(view="starred", tag="keep")["total"] == 1
    result = core.bulk_feeds([id], "delete")
    assert core.articles()["total"] == 0
    core.undo(result["undo"])
    assert core.articles()["total"] == 1


def test_dedup_updated_entries_and_index(core, site):
    id = subscription(core, site)
    original = core.articles()["items"][0]
    same = core.ingest(id, "changed-guid", "same", original["url"], "different body")
    assert same == original["id"]
    core.ingest(id, "one", "Publisher correction", original["url"], "<p>Heliotrope correction</p>")
    assert core.articles(q="heliotrope")["total"] == 1
    assert core.articles(q="independent")["total"] == 0
    second = core.subscribe(site[0] + "/json")["id"]
    core.ingest(second, "copy", "Publisher correction", site[0] + "/copy", "<p>Heliotrope correction</p>")
    assert core.articles()["total"] == 1
    assert core.articles(deduplicate=False)["total"] == 2
    assert core.articles(q='" OR title: * !!')["total"] == 0


def test_extraction_rules_transcript_and_markdown(core, site):
    id = subscription(core, site)
    a = core.articles()["items"][0]
    core.edit_feed(id, options={"selector": "article", "transcript_selector": ".transcript"})
    core.save_rule("Paywalls", "tags", "paywall", "read")
    core.save_rule("Rewrite", "body", "observatories", "rewrite", "telescopes")
    core.extract_article(a["id"])
    result = core.article(a["id"])
    assert result["read"] and "paywall" in result["tags"]
    assert result["url"] == site[0] + "/canonical"
    assert "telescopes" in result["body"]
    assert core.articles(q="photosynthesis")["total"] == 1
    assert core.articles(q="quasar")["total"] == 1
    assert "source:" in core.obsidian(a["id"])
    core.ingest(id, "one", "Updated feed title", a["url"], "new short summary")
    assert "telescopes" in core.article(a["id"])["body"]


def test_provided_podcast_transcript_and_download(core, site):
    base, routes, _ = site
    routes["/podcast"] = (
        200,
        {"Content-Type": "application/rss+xml"},
        f'''<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><title>Podcast</title><description>Audio</description><item><guid>episode1</guid><title>An episode</title><link>{base}/episode</link><enclosure url="{base}/audio.mp3" type="audio/mpeg" length="4"/><podcast:transcript url="{base}/transcript.txt" type="text/plain"/></item></channel></rss>''',
    )
    routes["/transcript.txt"] = (
        200,
        {"Content-Type": "text/plain"},
        "A provided transcript about astrobiology.",
    )
    routes["/audio.mp3"] = (200, {"Content-Type": "audio/mpeg"}, b"ID3test")
    subscription(core, site, "/podcast")
    a = core.article(core.articles()["items"][0]["id"])
    assert "astrobiology" in a["transcript"]
    assert core.articles(q="astrobiology")["total"] == 1
    job = core.enqueue("download", {"article_id": a["id"], "url": base + "/audio.mp3"})
    for _ in range(10):
        core.tick()
    assert core.download_path(job["id"]).read_bytes() == b"ID3test"
    backup = core.backup()
    with Core(core.root / "restored-audio") as restored:
        restored.restore(backup)
        assert restored.download_path(job["id"]).read_bytes() == b"ID3test"
        assert restored.articles(q="astrobiology")["total"] == 1
    core.delete_download(job["id"])
    assert not (core.root / "downloads" / job["id"]).exists()


def test_rules_drop_star_tag_and_regex_timeout(core, site):
    id = subscription(core, site)
    core.save_rule("Shorts", "url", "/shorts/", "read")
    core.save_rule("Save", "title", "special", "star")
    core.save_rule("Tag", "title", "special", "tag", "ideas")
    core.save_rule("Drop", "title", "advertisement", "drop")
    a = core.ingest(id, "short", "special", "https://www.youtube.com/shorts/abcdefghijk")
    assert core.article(a)["read"] and core.article(a)["starred"]
    assert core.article(a)["tags"] == ["ideas"]
    core.ingest(id, "ad", "Advertisement", site[0] + "/ad")
    assert core.articles(q="advertisement")["total"] == 0
    with pytest.raises(Exception):
        core.save_rule("Invalid", "title", "[", "read")


def test_opml_nested_roundtrip_and_backup_restore(core, site, tmp_path):
    base, _, _ = site
    xml = f'''<opml version="2.0"><body><outline text="Science"><outline text="Space"><outline text="Custom journal" type="rss" xmlUrl="{base}/rss"/></outline></outline></body></opml>'''
    assert core.import_opml(xml)["imported"] == 1
    core.refresh(core.feeds()[0]["id"])
    a = core.articles()["items"][0]
    core.change_articles([a["id"]], starred=True, read=True, tags=["science"])
    core.save_settings({"theme": "dark"})
    backup = core.backup()
    with Core(tmp_path / "restored") as restored:
        assert restored.restore(backup)["restored"] == 1
        assert restored.article(a["id"])["starred"] == 1
        assert restored.settings()["theme"] == "dark"
        assert restored.articles(q="independent")["total"] == 1
        assert restored.export_opml() == core.export_opml()
        with pytest.raises(ValueError, match="empty"):
            restored.restore(backup)
    with Core(tmp_path / "opml") as imported:
        imported.import_opml(core.export_opml())
        folders = imported.folders()
        space = next(f for f in folders if f["name"] == "Space")
        assert next(f for f in folders if f["id"] == space["parent_id"])["name"] == "Science"


def test_retention_protects_unread_saved_archive_and_tombstones(core, site):
    id = subscription(core, site)
    a = core.articles()["items"][0]
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    with core.db:
        core.db.execute("UPDATE articles SET added=?", (old,))
    assert core.purge()["purged"] == 0
    core.change_articles([a["id"]], read=True, starred=True)
    assert core.purge()["purged"] == 0
    core.change_articles([a["id"]], starred=False)
    core.edit_feed(id, options={"archive": True})
    assert core.purge()["purged"] == 0
    core.edit_feed(id, options={"archive": False})
    assert core.purge()["purged"] == 1
    core.refresh(id)
    assert core.articles()["total"] == 0


def test_chrome_read_state_conflicts_and_undo(core, site):
    timestamp = int((datetime.now(timezone.utc) - timedelta(minutes=2)).timestamp() * 1000)
    entry = {
        "url": site[0] + "/article?utm_source=chrome",
        "title": "Chrome article",
        "hasBeenRead": False,
        "lastUpdateTime": timestamp,
    }
    core.chrome_sync([entry])
    a = core.articles()["items"][0]
    result = core.change_articles([a["id"]], read=True)
    assert core.chrome_sync([entry])["entries"][0]["hasBeenRead"]
    core.undo(result["undo"])
    assert not core.chrome_sync([entry])["entries"][0]["hasBeenRead"]
    entry.update(lastUpdateTime=int(datetime.now(timezone.utc).timestamp() * 1000) + 30000, hasBeenRead=True)
    assert core.chrome_sync([entry])["entries"][0]["hasBeenRead"]


def test_worker_health_failures_and_unique_jobs(core, site):
    id = core.subscribe(site[0] + "/missing")["id"]
    one = core.enqueue("refresh", {"feed_id": id})
    two = core.enqueue("refresh", {"feed_id": id})
    assert one == two
    core.tick()
    assert core.feeds()[0]["health"] == "error"
    assert core.feeds()[0]["failures"] == 1
    assert core.overview()["jobs"][0]["status"] in ("failed", "queued")


def test_site_scraper_and_feed_backfill(core, site):
    base, routes, _ = site
    routes["/no-feed"] = (
        200,
        {"Content-Type": "text/html"},
        '<html><article class="story"><a href="/older">An older story</a></article></html>',
    )
    id = core.subscribe(base + "/no-feed", options={"scrape_selector": "article.story"})["id"]
    core.refresh(id)
    assert core.articles()["items"][0]["url"] == base + "/older"
    routes["/archive"] = (
        200,
        {"Content-Type": "application/feed+json"},
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "Archive",
                "next_url": "/json",
                "items": [{"id": "old", "title": "Old archive item", "url": base + "/old-item"}],
            }
        ),
    )
    assert core.backfill(id, base + "/archive")["pages"] == 2
    assert core.articles()["total"] == 3
    assert len(core.feeds()) == 1


def test_untrusted_html_private_network_and_embed(monkeypatch):
    cleaned = sanitize(
        '<script>alert(1)</script><img src="/pixel" width="1"><img src="/image" onerror="alert(2)"><a href="javascript:alert(3)">bad</a><a href="/page?utm_source=x&ok=1">good</a><iframe src="https://evil.test"></iframe>',
        "https://example.com",
    )
    assert "<script" not in cleaned and "onerror" not in cleaned and "/pixel" not in cleaned
    assert "javascript:" not in cleaned and "iframe" not in cleaned and "utm_source" not in cleaned
    assert "ok=1" in cleaned
    monkeypatch.delenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", raising=False)
    with pytest.raises(ValueError, match="Private network"):
        safe_url("http://127.0.0.1/test")
    with pytest.raises(ValueError):
        safe_url("file:///etc/passwd")
    assert embed_url("https://youtu.be/abcdefghijk") == "https://www.youtube-nocookie.com/embed/abcdefghijk"
    assert embed_url("https://evil.test/?v=abcdefghijk") is None


def test_architecture_reader_imports_only_in_core():
    for path in Path("feedelio").rglob("*.py"):
        if "core" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(not x.name.startswith("reader") for x in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("reader"), path
