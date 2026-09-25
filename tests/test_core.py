import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from feedelio.core import BACKUP_TABLES, Core, now
from feedelio.core.content import embed_url, safe_url, same_feed, sanitize
from feedelio.core.reader_extensions import duration_seconds


def subscription(core, site, path="/rss"):
    base, _, _ = site
    core.save_settings({"new_feed_read": False})  # These tests exercise the unread workflow.
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


def test_redirect_onto_an_existing_subscription_keeps_both_urls(core, site):
    base, routes, _ = site
    routes["/moved"] = (301, {"Location": "/rss"}, "")
    core.subscribe(base + "/rss")
    id = core.subscribe(base + "/moved")["id"]
    core.refresh(id)
    moved = next(f for f in core.feeds() if f["id"] == id)
    assert moved["url"] == base + "/moved" and moved["error"] is None
    assert sorted(f["url"] for f in core.feeds()) == [base + "/moved", base + "/rss"]


def test_brotli_feed_served_without_negotiation_is_decoded(core, site):
    import brotli

    base, routes, _ = site
    # Some CDNs send Brotli regardless of Accept-Encoding; the decoder must be present.
    routes["/br"] = (
        200,
        {"Content-Type": "application/rss+xml", "Content-Encoding": "br"},
        brotli.compress(routes["/rss"][2].encode()),
    )
    id = subscription(core, site, "/br")
    core.refresh(id)
    assert core.feeds()[0]["error"] is None and core.articles()["total"] == 1


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
    # Deleting a folder lifts its subfolders, unless the parent has one by that name.
    twin = core.save_folder("Engineering")["id"]
    with pytest.raises(ValueError, match="Move or rename"):
        core.delete_folder(parent)
    core.delete_folder(twin)
    core.save_settings({"collapsed_folders": [child]})
    core.delete_folder(child)
    assert core.feeds()[0]["folder_id"] == "inbox"
    assert core.settings()["collapsed_folders"] == []
    # Sibling folder names are unique, case-insensitively; the same name may recur elsewhere.
    with pytest.raises(ValueError, match="already exists"):
        core.save_folder("technology")
    with pytest.raises(ValueError, match="already exists"):
        core.save_folder("Unfiled")
    core.save_folder("Technology", None, parent)
    core.save_folder("Technology", parent)
    core.save_folder("Reading", parent)
    imported = core.import_opml(
        f'<opml><body><outline text="Technology"><outline text="reading"><outline text="Twin" xmlUrl="{site[0]}/json"/></outline></outline></body></opml>'
    )
    assert imported["imported"] == 1
    reading = [f for f in core.folders() if f["name"].lower() == "reading"]
    assert len(reading) == 1 and reading[0]["parent_id"] == parent
    assert core.feeds()[1]["folder_id"] == reading[0]["id"]


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
    assert core.articles(q="Heliotrop corr")["total"] == 1  # Words match as typed.
    assert core.articles(q="independent")["total"] == 0
    with pytest.raises(ValueError, match="Already subscribed"):
        core.subscribe(site[0] + "/rss ")
    with pytest.raises(ValueError, match="Already subscribed"):
        core.subscribe(site[0].replace("http://", "https://") + "/rss/")
    assert same_feed("http://www.example.com/feed/", "https://Example.com/feed")
    assert not same_feed("https://example.com/feed?x=1", "https://example.com/feed")
    assert not same_feed("https://blog.example.com/feed", "https://example.com/feed")
    second = core.subscribe(site[0] + "/json")["id"]
    core.ingest(second, "copy", "Publisher correction", site[0] + "/copy", "<p>Heliotrope correction</p>")
    assert core.articles()["total"] == 1
    assert core.articles(deduplicate=False)["total"] == 2
    assert core.articles(q='" OR title: * !!')["total"] == 0


def test_episodes_sharing_the_show_page_link_stay_separate(core, site):
    id = subscription(core, site)
    home = site[0] + "/podcast"

    def mp3(n):
        return [{"href": site[0] + f"/{n}.mp3", "type": "audio/mpeg"}]

    first = core.ingest(id, "ep-1", "Episode one", home, "<p>Notes one</p>", enclosures=mp3(1))
    second = core.ingest(id, "ep-2", "Episode two", home, "<p>Notes two</p>", enclosures=mp3(2))
    assert first != second
    # A refresh sees every episode again; each must find its own article.
    third = core.ingest(id, "ep-3", "Episode three", home, "<p>Notes three</p>", enclosures=mp3(3))
    for n, expected in ((1, first), (2, second), (3, third)):
        ep = core.ingest(id, f"ep-{n}", f"Episode {n}", home, f"<p>Notes {n}</p>", enclosures=mp3(n))
        assert ep == expected
    # The same episode reissued with a new id but the same audio is still one article.
    assert core.ingest(id, "ep-2-v2", "Episode two", home, "<p>Notes two</p>", enclosures=mp3(2)) == second
    # Two title-only items (a saved page, a linkless entry) are not the same article.
    assert core.ingest(id, "https://one.example/", "Dashboard", "https://one.example/") != core.ingest(
        id, "https://two.example/", "Dashboard", "https://two.example/"
    )
    # A plain article that only moved to a new id keeps matching by link.
    post = core.ingest(id, "post-a", "A longer article title", site[0] + "/post", "<p>Body</p>")
    assert core.ingest(id, "post-b", "A longer article title", site[0] + "/post", "<p>Edited</p>") == post


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
        f'''<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><title>Podcast</title><description>Audio</description><item><guid>episode1</guid><title>An episode</title><link>{base}/episode</link><itunes:duration xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">1:02:03</itunes:duration><enclosure url="{base}/audio.mp3" type="audio/mpeg" length="4"/><podcast:transcript url="{base}/transcript.txt" type="text/plain"/></item></channel></rss>''',
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
    assert a["duration"] == 3723  # itunes:duration, shown as length instead of reading time
    assert duration_seconds("62:03") == 3723 and duration_seconds("90") == 90 and duration_seconds("x") == 0
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
    core.save_settings({"retention_days": 90})  # Retention is off by default.
    a = core.articles()["items"][0]
    # A downloaded episode goes with its article.
    while core.tick():  # The subscription's own refresh would reset the enclosures.
        pass
    with core.db:
        core.db.execute(
            "UPDATE articles SET enclosures=? WHERE id=?",
            (json.dumps([{"href": site[0] + "/audio.mp3", "type": "audio/mpeg"}]), a["id"]),
        )
    job = core.enqueue("download", {"article_id": a["id"], "url": site[0] + "/audio.mp3"})
    site[1]["/audio.mp3"] = (200, {"Content-Type": "audio/mpeg"}, b"ID3test")
    for _ in range(10):
        core.tick()
    assert core.download_path(job["id"]).exists()
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
    assert not (core.root / "downloads" / job["id"]).exists()
    assert not [d for d in core.overview()["downloads"] if d["status"] == "done"]
    core.refresh(id)
    assert core.articles()["total"] == 0


def test_saved_pages_sharing_a_title_stay_separate(core, site):
    entries = [
        {"url": f"https://{host}.example/", "title": "Dashboard", "hasBeenRead": read, "lastUpdateTime": 1}
        for host, read in (("one", False), ("two", True))
    ]
    core.chrome_sync(entries)
    items = core.articles(view="reading", deduplicate=False)["items"]
    assert sorted(a["url"] for a in items) == ["https://one.example/", "https://two.example/"]
    assert [a["hasBeenRead"] for a in core.chrome_sync(entries)["entries"]] == [False, True]


def test_chrome_read_state_conflicts_and_undo(core, site):
    timestamp = int((datetime.now(timezone.utc) - timedelta(minutes=2)).timestamp() * 1000)
    entry = {
        "url": site[0] + "/article?utm_source=chrome",
        "title": "Chrome article",
        "hasBeenRead": False,
        "lastUpdateTime": timestamp,
    }
    core.chrome_sync([entry])
    a = core.articles(view="reading")["items"][0]
    result = core.change_articles([a["id"]], read=True)
    assert core.chrome_sync([entry])["entries"][0]["hasBeenRead"]
    core.undo(result["undo"])
    assert not core.chrome_sync([entry])["entries"][0]["hasBeenRead"]
    entry.update(lastUpdateTime=int(datetime.now(timezone.utc).timestamp() * 1000) + 30000, hasBeenRead=True)
    assert core.chrome_sync([entry])["entries"][0]["hasBeenRead"]


def test_the_reading_list_is_a_view_of_its_own(core, site):
    # Reading-list pages are not subscriptions: no feed in the list, nothing in
    # All articles, and each is listed under the website it is on.
    feed = core.subscribe(site[0] + "/rss", "inbox", "Real Feed")["id"]
    core.ingest(feed, "f1", "Feed story", site[0] + "/f1", "<p>Body.</p>")
    entries = [
        {
            "url": "https://www.example.org/a",
            "title": "Saved page",
            "hasBeenRead": False,
            "lastUpdateTime": 1,
        },
        {"url": "https://news.example.net/b", "title": "Read page", "hasBeenRead": True, "lastUpdateTime": 1},
    ]
    core.chrome_sync(entries)
    overview = core.overview()
    assert [f["title"] for f in overview["feeds"]] == ["Real Feed"]
    assert overview["counts"]["unread"] == 1 and overview["counts"]["reading"] == 1
    assert [a["title"] for a in core.articles()["items"]] == ["Feed story"]
    assert core.articles(folder_id="inbox")["total"] == 1
    reading = core.articles(view="reading")["items"]
    assert {a["title"]: a["feed_title"] for a in reading} == {
        "Saved page": "example.org",
        "Read page": "news.example.net",
    }
    assert core.article(reading[0]["id"])["feed_title"] in ("example.org", "news.example.net")
    # Saved and History are about articles, so a starred page shows in Saved.
    core.change_articles([reading[0]["id"]], starred=True)
    assert core.articles(view="starred")["total"] == 1
    # Marking all read in the view touches the reading list only.
    core.mark_all(view="reading")
    assert core.overview()["counts"] == {**core.overview()["counts"], "unread": 1, "reading": 0}
    assert all(e["hasBeenRead"] for e in core.chrome_sync(entries)["entries"])

    # Removed in Chrome: gone from the view. Added back: there again.
    core.chrome_sync(entries[:1])
    assert [a["title"] for a in core.articles(view="reading")["items"]] == ["Saved page"]
    back = {**entries[1], "hasBeenRead": False, "lastUpdateTime": 2}
    core.chrome_sync([entries[0], back])
    assert core.articles(view="reading", unread=True)["total"] == 1


def test_a_reading_list_page_deleted_here_is_removed_from_chrome(core, site):
    entries = [
        {"url": f"https://example.org/{n}", "title": n, "hasBeenRead": False, "lastUpdateTime": 1}
        for n in ("keep", "copy")
    ]
    core.chrome_sync(entries)
    copy = next(a for a in core.articles(view="reading")["items"] if a["title"] == "copy")
    core.change_articles([copy["id"]], deleted=True)
    # Chrome still lists it: it stays deleted here and Chrome is asked to remove it.
    result = core.chrome_sync(entries)
    assert result["remove"] == ["https://example.org/copy"]
    assert [e["url"] for e in result["entries"]] == ["https://example.org/keep"]
    assert [a["title"] for a in core.articles(view="reading")["items"]] == ["keep"]
    # Once Chrome has removed it, adding it there again brings it back.
    assert core.chrome_sync(entries[:1])["remove"] == []
    assert core.chrome_sync(entries)["remove"] == []
    assert sorted(a["title"] for a in core.articles(view="reading")["items"]) == ["copy", "keep"]


def test_reading_list_pages_are_dated_when_added_and_opened_ones_extract_first(core, monkeypatch):
    entries = [
        {
            "url": f"https://example.org/{n}",
            "title": f"Page {n}",
            "hasBeenRead": False,
            "lastUpdateTime": 1,
            "creationTime": added,
        }
        for n, added in (("old", 1_700_000_000_000), ("new", 1_760_000_000_000), ("mid", 1_730_000_000_000))
    ]
    # Pages imported before dates were sent get theirs on the next sync.
    core.chrome_sync([{k: v for k, v in e.items() if k != "creationTime"} for e in entries])
    core.chrome_sync(entries)
    items = core.articles(view="reading")["items"]
    assert [a["title"] for a in items] == ["Page new", "Page mid", "Page old"]
    assert items[0]["published"].startswith("2025-10-09")

    # Every page waits for its text; the one being read jumps the queue.
    old = items[-1]["id"]
    assert core.article(old)["extracting"]
    ran = []
    monkeypatch.setattr(core, "extract_article", lambda article_id: ran.append(article_id) or {})
    core.enqueue("extract", {"article_id": old, "asked": True})
    core.tick()
    assert ran == [old] and not core.article(old)["extracting"]


def test_feed_health_is_decided_once_for_the_whole_app(core, site):
    # The library filter, the sidebar and the feed list all read this verdict,
    # so a feed with nothing new for a fortnight is stale everywhere at once.
    id = subscription(core, site)
    assert core.feeds()[0]["health"] == "healthy"
    with core.db:
        core.db.execute("UPDATE feeds SET last_article=? WHERE id=?", (now(days=-15), id))
    assert core.feeds()[0]["health"] == "stale"
    with core.db:
        core.db.execute("UPDATE feeds SET last_article=? WHERE id=?", (now(), id))
        core.db.execute("UPDATE articles SET tags='[\"paywall\"]' WHERE feed_id=?", (id,))
    assert core.feeds()[0]["health"] == "partial"
    with core.db:
        core.db.execute("UPDATE feeds SET checked=NULL,last_article=NULL WHERE id=?", (id,))
        core.db.execute("UPDATE articles SET tags='[]' WHERE feed_id=?", (id,))
    assert core.feeds()[0]["health"] == "pending"


def test_backup_and_restore_agree_on_the_tables_they_carry(core, site):
    subscription(core, site)
    backup = core.backup()
    assert set(backup["tables"]) == set(BACKUP_TABLES)
    with Core(core.root / "copy") as copy:
        copy.restore(backup)
        assert copy.feeds()[0]["url"] == core.feeds()[0]["url"]


def test_worker_health_failures_and_unique_jobs(core, site):
    id = core.subscribe(site[0] + "/missing")["id"]
    one = core.enqueue("refresh", {"feed_id": id})
    two = core.enqueue("refresh", {"feed_id": id})
    assert one == two
    core.tick()
    assert core.feeds()[0]["health"] == "error"
    assert core.feeds()[0]["failures"] == 1
    assert core.overview()["jobs"][0]["status"] in ("failed", "queued")


def test_refresh_jobs_run_before_an_older_extraction_backlog(core, site):
    id = subscription(core, site)
    core.db.execute("UPDATE jobs SET status='done'")  # Clear the subscription's own jobs.
    core.db.commit()
    for a in core.articles()["items"]:
        core.enqueue("extract", {"article_id": a["id"]})
    refresh = core.enqueue("refresh", {"feed_id": id})["id"]
    core.tick()
    ran = core.rows("SELECT id, kind FROM jobs WHERE status='done' ORDER BY updated DESC LIMIT 1")
    assert ran[0]["id"] == refresh and ran[0]["kind"] == "refresh"


def test_manual_refresh_jumps_a_queued_library_refresh(core, site):
    first = core.subscribe(site[0] + "/rss")["id"]
    second = core.subscribe(site[0] + "/json")["id"]
    core.db.execute("UPDATE jobs SET status='done'")
    core.db.commit()
    core.refresh_all()  # Queues first, then second, in library order.
    queued = core.enqueue("refresh", {"feed_id": second})  # A scheduled duplicate waits its turn.
    manual = core.enqueue("refresh", {"feed_id": second, "manual": True})
    assert queued == manual  # Promoted, not duplicated.
    core.tick()
    ran = core.rows("SELECT payload FROM jobs WHERE status='done' ORDER BY updated DESC LIMIT 1")
    assert ran[0]["payload"] == {"feed_id": second, "manual": True}
    core.tick()
    assert core.rows("SELECT payload FROM jobs WHERE status='done' ORDER BY updated DESC LIMIT 1")[0][
        "payload"
    ] == {"feed_id": first}
    with pytest.raises(ValueError, match="Unknown refresh option"):
        core.enqueue("refresh", {"feed_id": first, "url": "x"})


def test_rate_limited_extraction_is_retried_later(core, site):
    _, routes, _ = site
    subscription(core, site)
    article = core.articles()["items"][0]["id"]
    routes["/article"] = (429, {"Retry-After": "120"}, "slow down")
    core.db.execute("UPDATE jobs SET status='done'")
    core.db.commit()
    job = core.enqueue("extract", {"article_id": article})["id"]
    for _ in range(6):  # The tick also seeds purge and archive jobs; drain them.
        core.tick()
    row = core.one("SELECT * FROM jobs WHERE id=?", (job,))
    assert row["status"] == "queued" and row["result"]["attempts"] == 1 and "429" in row["error"]
    assert row["result"]["not_before"] > (datetime.now(timezone.utc) + timedelta(seconds=100)).isoformat()
    assert core.one("SELECT * FROM jobs WHERE id=?", (job,))["result"]["attempts"] == 1  # Not due yet.
    routes["/article"] = (
        200,
        {"Content-Type": "text/html"},
        "<html><body><article><p>" + "Full text. " * 40 + "</p></article></body></html>",
    )
    core.db.execute("UPDATE jobs SET result=json_set(result,'$.not_before','2000-01-01') WHERE id=?", (job,))
    core.db.commit()
    core.tick()
    assert core.one("SELECT status FROM jobs WHERE id=?", (job,))["status"] == "done"
    assert "Full text." in core.article(article)["body"]


def test_youtube_entries_keep_their_description_and_learn_shorts(core, site, monkeypatch, tmp_path):
    from feedelio import core as module

    base, routes, _ = site
    routes["/yt"] = (
        200,
        {"Content-Type": "application/atom+xml"},
        """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
        <title>Channel</title><id>yt:channel:x</id>
        <entry><id>yt:video:abcdefghijk</id><title>A short one</title>
          <link rel="alternate" href="https://www.youtube.com/watch?v=abcdefghijk"/>
          <media:group><media:description>Subscribe: https://example.test/

Second paragraph</media:description></media:group></entry>
        <entry><id>yt:video:zyxwvutsrqp</id><title>A long one</title>
          <link rel="alternate" href="https://www.youtube.com/watch?v=zyxwvutsrqp"/>
          <media:group><media:description>Plain</media:description></media:group></entry>
        </feed>""",
    )
    probed = []

    def probe(url, options=None):
        probed.append(url)
        short = "abcdefghijk" in url
        return dict(
            short=short,
            url="https://www.youtube.com/shorts/abcdefghijk" if short else url,
            duration=58 if short else 3725,
        )

    monkeypatch.setattr(module, "probe_video", probe)
    monkeypatch.setattr(module, "extract", lambda *a, **k: pytest.fail("Video pages must not be extracted"))
    core.save_rule("Skip Shorts", "url", r"youtube\.com/shorts/", "read", id="shorts")
    core.save_settings({"new_feed_read": False})
    id = core.subscribe(base + "/yt")["id"]
    core.refresh(id)
    while core.tick():
        pass
    long, short = sorted(core.articles()["items"], key=lambda a: a["title"])
    assert len(probed) == 2
    assert short["url"] == "https://www.youtube.com/shorts/abcdefghijk" and short["tags"] == ["short"]
    assert short["read"] == 1 and long["read"] == 0 and long["tags"] == []
    body = core.article(short["id"])["body"]
    assert '<a href="https://example.test/"' in body and "<p>Second paragraph</p>" in body
    assert core.article(long["id"])["embed"] == "https://www.youtube-nocookie.com/embed/zyxwvutsrqp"
    assert core.article(long["id"])["duration"] == 3725 and core.article(short["id"])["duration"] == 58
    # A backup from before the duration column restores with the default.
    backup = core.backup()
    for row in backup["tables"]["articles"]:
        del row["duration"]
    with Core(tmp_path / "older") as restored:
        assert restored.restore(backup)["restored"] == 2
        assert restored.article(long["id"])["duration"] == 0


def test_bookmarked_feeds_form_their_own_stream(core, site):
    base, _, _ = site
    id = subscription(core, site)
    other = core.subscribe(base + "/json")["id"]
    core.refresh(other)
    assert core.articles(view="bookmarks")["total"] == 0
    core.edit_feed(id, options={"bookmarked": True})
    stream = core.articles(view="bookmarks")
    assert stream["total"] == 1 and stream["items"][0]["feed_id"] == id
    assert core.overview()["counts"]["bookmarks"] == 1
    with pytest.raises(ValueError, match="boolean"):
        core.edit_feed(id, options={"bookmarked": "yes"})


def test_site_scraper_and_feed_backfill(core, site):
    base, routes, _ = site
    routes["/no-feed"] = (
        200,
        {"Content-Type": "text/html"},
        '<html><article class="story"><a href="/older">An older story</a></article>'
        '<article class="story"><span>2019-05-04</span> <a href="/dated">A dated story</a></article></html>',
    )
    id = core.subscribe(base + "/no-feed", options={"scrape_selector": "article.story"})["id"]
    core.refresh(id)
    assert core.articles()["items"][0]["url"] == base + "/older"
    dated = core.articles()["items"][1]  # Listing dates order the archive, not the scrape time.
    assert dated["url"] == base + "/dated" and dated["published"].startswith("2019-05-04")
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
    state = core.backfill(id, base + "/archive")
    assert state["pages"] == 1
    assert core.backfill(id, state=state)["pages"] == 2
    assert core.articles()["total"] == 4
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


def test_ingest_keeps_only_web_urls(core, site):
    base, routes, _ = site
    routes["/scrape-bad"] = (
        200,
        {"Content-Type": "text/html"},
        '<html><article class="story"><a href="javascript:alert(1)">Scripted link</a></article>'
        '<article class="story"><a href="/fine">Fine link</a></article></html>',
    )
    routes["/json-bad"] = (
        200,
        {"Content-Type": "application/feed+json"},
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "Bad JSON",
                "items": [
                    {
                        "id": "js",
                        "title": "Scripted JSON story",
                        "url": "javascript:alert(2)",
                        "content_text": "text",
                        "attachments": [
                            {"url": "javascript:alert(3)", "mime_type": "audio/mpeg"},
                            {"url": base + "/episode.mp3", "mime_type": "audio/mpeg"},
                        ],
                    }
                ],
            }
        ),
    )
    scraped = core.subscribe(base + "/scrape-bad", options={"scrape_selector": "article.story"})["id"]
    core.refresh(scraped)
    parsed = core.subscribe(base + "/json-bad")["id"]
    core.refresh(parsed)
    urls = {a["title"]: a["url"] for a in core.articles()["items"]}
    assert urls["Scripted link"] == "" and urls["Fine link"] == base + "/fine"
    assert urls["Scripted JSON story"] == ""
    story = next(a for a in core.articles()["items"] if a["title"] == "Scripted JSON story")
    assert [e["href"] for e in core.article(story["id"])["enclosures"]] == [base + "/episode.mp3"]
    assert core.article(story["id"])["url"] == base + "/json-bad"


def test_feed_option_validation(core, site):
    id = subscription(core, site)
    for bad in (
        {"retention_days": "abc"},
        {"retention_days": -1},
        {"retention_days": True},
        {"archive": "on"},
    ):
        with pytest.raises(ValueError):
            core.edit_feed(id, options=bad)
    core.edit_feed(id, options={"retention_days": 0, "archive": True, "extract": False})
    core.edit_feed(id, options={"retention_days": None})
    assert core.purge()["purged"] == 0


def test_core_opens_without_schema_writes_and_caches_frame_sources(core):
    import sqlite3

    with sqlite3.connect(core.root / "app.sqlite", timeout=0) as other:
        other.execute("BEGIN IMMEDIATE")
        with Core(core.root) as again:  # A concurrent writer must not delay opening.
            assert again.frame_sources() == "https:"
    core.save_settings({"invidious": "http://127.0.0.1:3000"})
    assert Core(core.root).frame_sources() == "https: http://127.0.0.1:3000"


def test_feeds_extract_full_articles_by_default(core, site):
    base, _, _ = site
    jobs = "SELECT count(*) n FROM jobs WHERE kind='extract'"
    id = core.subscribe(base + "/rss")["id"]
    core.refresh(id)
    assert core.one(jobs)["n"] == 1
    core.save_settings({"extract_articles": False})
    id = core.subscribe(base + "/json")["id"]
    core.refresh(id)
    assert core.one(jobs)["n"] == 1
    core.refresh(core.subscribe(base + "/atom", options={"extract": True})["id"])
    assert core.one(jobs)["n"] == 2


def test_statistics_report_archive_size(core, site):
    subscription(core, site)
    (core.root / "downloads" / "episode").write_bytes(b"x" * 1000)
    archive = core.statistics()["archive"]
    assert archive["downloads"] == 1
    assert archive["bytes"] > 1000 + (core.root / "app.sqlite").stat().st_size


def test_opml_import_keeps_feeds_whose_host_no_longer_resolves(core, monkeypatch):
    import socket

    from feedelio.core import content

    def resolve(host, port):
        if host == "gone.test":
            raise socket.gaierror(-5, "No address associated with hostname")
        return ["93.184.216.34"]

    monkeypatch.delenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", raising=False)
    monkeypatch.setattr(content, "addresses", resolve)
    xml = """<opml version="1.0"><body><outline text="Blogs">
      <outline text="Alive" type="rss" xmlUrl="https://alive.test/feed"/>
      <outline text="Gone" type="rss" xmlUrl="https://gone.test/feed"/>
    </outline></body></opml>"""
    assert core.import_opml(xml) == {"imported": 2, "unresolved": 1}
    assert sorted(f["title"] for f in core.feeds()) == ["Alive", "Gone"]
    with pytest.raises(ValueError, match="gone.test could not be resolved"):
        core.subscribe("https://gone.test/other")

    def private(host, port):
        raise ValueError("Private network addresses are disabled.")

    monkeypatch.setattr(content, "addresses", private)
    with pytest.raises(ValueError, match="Private network"):
        core.import_opml(xml)


def test_a_feed_can_arrive_read_and_podcasts_are_marked(core, site):
    base, _, _ = site
    id = subscription(core, site)
    before = core.ingest(id, "before", "Before the switch", base + "/before")
    core.edit_feed(id, options={"mark_read": True})
    after = core.ingest(id, "after", "After the switch", base + "/after")
    assert not core.article(before)["read"] and core.article(after)["read"]
    with pytest.raises(ValueError):
        core.edit_feed(id, options={"mark_read": "yes"})
    core.edit_feed(id, options={"mark_read": False})
    typed = core.ingest(
        id, "ep1", "Typed episode", base + "/1", enclosures=[{"href": base + "/a", "type": "audio/mpeg"}]
    )
    bare = core.ingest(id, "ep2", "Untyped episode", base + "/2", enclosures=[{"href": base + "/ep.MP3?x=1"}])
    image = core.ingest(
        id, "img", "A picture", base + "/3", enclosures=[{"href": base + "/p.jpg", "type": "image/jpeg"}]
    )
    audio = {a["id"]: a["audio"] for a in core.articles(limit=50)["items"]}
    assert audio[typed] and audio[bare] and not audio[image] and not audio[before]
