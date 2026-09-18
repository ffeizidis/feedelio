import json
import re
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from playwright.sync_api import expect


@pytest.mark.features(18, 5)
def test_changing_manual_interval_reschedules_existing_feed(lab):
    now = datetime.now(timezone.utc)
    entries = [
        {
            "title": f"Scheduled story {i}",
            "extra": f"<pubDate>{format_datetime(now - timedelta(hours=i * 4))}</pubDate>",
        }
        for i in range(6)
    ]
    lab.rss("/schedule", "Schedule", entries)
    lab.show()
    lab.upload(
        f'<opml><body><outline text="Schedule" type="rss" xmlUrl="{lab.publisher}/schedule"/></body></opml>'
    )
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 1)
    lab.finish_jobs()
    before = lab.api("overview")["feeds"][0]
    assert before["interval"] == 120, before
    lab.manager("Subscriptions")
    p = lab.page
    p.get_by_role("button", name="Edit Schedule", exact=True).click()
    p.get_by_role("spinbutton", name=re.compile("Refresh interval")).fill("17")
    p.get_by_role("button", name="Save feed settings", exact=True).click()
    lab.until(lambda: lab.api("overview")["feeds"][0]["options"].get("interval") == 17)
    after = lab.api("overview")["feeds"][0]
    assert datetime.fromisoformat(after["next_check"]) <= datetime.now(timezone.utc) + timedelta(
        minutes=18
    ), "Manual refresh setting left the previous two-hour schedule in place"


@pytest.mark.features(30)
def test_feed_fetch_uses_real_http_proxy(lab):
    calls = []

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            with httpx.Client(trust_env=False) as client:
                response = client.get(self.path)
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("content-type", "application/rss+xml"))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *_):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        lab.seed(articles=[{"title": "Proxy story"}])
        lab.show()
        lab.manager("Subscriptions")
        p = lab.page
        p.get_by_role("button", name="Edit Audit feed", exact=True).click()
        p.get_by_role("textbox", name="HTTP proxy", exact=True).fill(f"http://127.0.0.1:{proxy.server_port}")
        p.get_by_role("button", name="Save feed settings", exact=True).click()
        lab.close()
        p.get_by_role("button", name="Refresh feeds (R)", exact=True).click()
        lab.finish_jobs()
        assert lab.publisher + "/feed" in calls
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join()


@pytest.mark.features(19)
def test_quiet_feed_detection_in_browser(lab):
    feed = lab.seed("Quiet journal", [{"title": "Old story"}])
    with lab.core.db:
        lab.core.db.execute(
            "UPDATE feeds SET checked=?,last_article=? WHERE id=?",
            (
                datetime.now(timezone.utc).isoformat(),
                (datetime.now(timezone.utc) - timedelta(days=100)).isoformat(),
                feed,
            ),
        )
    lab.show()
    lab.manager("Subscriptions")
    expect(lab.page.locator(".health")).to_have_text("Quiet for 90d")


@pytest.mark.features(8, 13, 20)
def test_pagination_search_and_filter_reset(lab):
    lab.seed(
        articles=[
            {"title": f"Pagination item {i:03d}", "url": lab.publisher + f"/story/{i}"} for i in range(105)
        ]
    )
    lab.show()
    p = lab.page
    expect(p.locator(".article-row")).to_have_count(100)
    p.locator(".pagination").get_by_role("button", name="Next", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(5)
    p.get_by_role("textbox", name="Search articles").fill("Pagination item 104")
    expect(p.locator(".article-row")).to_have_count(1)
    expect(p.locator(".article-row h3")).to_have_text("Pagination item 104")
    p.get_by_role("button", name="Clear search", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(100)


@pytest.mark.features(32, 34)
def test_regex_timeout_does_not_wedge_worker_or_browser(lab):
    lab.seed(articles=[{"title": "Regex adversary", "body": "<p>" + "a" * 100000 + "!</p>"}])
    lab.show()
    p = lab.page
    lab.manager("Rules")
    form = p.locator(".rule-form")
    form.locator("input[name=name]").fill("Pathological regex")
    form.locator("input[name=pattern]").fill("(a+)+$")
    form.locator("select[name=field]").select_option("body")
    form.locator("select[name=action]").select_option("read")
    form.get_by_role("button", name="Add rule", exact=True).click()
    expect(p.locator(".rule-row")).to_have_count(1)
    p.get_by_role("button", name="Apply rules to existing articles", exact=True).click()
    lab.finish_jobs()
    failed = [j for j in lab.api("overview")["jobs"] if j["kind"] == "rules" and j["status"] == "failed"]
    assert failed and "timed out" in failed[0]["error"]
    lab.close()
    lab.open_title("Regex adversary")
    expect(p.locator(".prose")).to_be_visible()


@pytest.mark.features(43)
def test_invalid_restore_rolls_back_and_allows_retry(lab):
    backup = lab.core.backup()
    backup["tables"]["folders"].append({"id": "cycle", "name": "Cycle", "parent_id": "cycle"})
    lab.show()
    lab.manager("Data & connections")
    p = lab.page
    upload = p.locator('input[accept=".json"]')
    upload.set_input_files(
        {"name": "bad.json", "mimeType": "application/json", "buffer": json.dumps(backup).encode()}
    )
    expect(p.locator(".manager-body .inline-error")).to_contain_text("folder tree")
    assert lab.api("overview")["folders"] == [
        {"id": "inbox", "name": "Unfiled", "parent_id": None, "unread": 0}
    ]
    assert not upload.is_disabled()


@pytest.mark.features(12, 26, 27, 44)
def test_bulk_mark_read_does_not_claim_unopened_articles_as_reading_history(lab):
    lab.seed(
        articles=[
            {"title": "Never opened first"},
            {"title": "Never opened second", "url": lab.publisher + "/second"},
        ]
    )
    lab.show()
    p = lab.page
    p.get_by_role("button", name="Mark this stream as read", exact=True).click()
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 0)
    with p.expect_response(lambda r: "/api/articles?" in r.url and "view=history" in r.url) as response:
        p.locator(".views").get_by_role("button", name="History", exact=True).click()
    response.value.body()
    p.wait_for_timeout(100)
    expect(p.locator(".article-row")).to_have_count(0)


@pytest.mark.features(7, 9, 12, 14, 25, 26)
def test_feed_folder_mark_all_and_article_delete_undo(lab):
    parent = lab.core.save_folder("Parent")["id"]
    child = lab.core.save_folder("Child", parent)["id"]
    lab.seed("Scoped one", [{"title": "Scoped story one"}], child)
    lab.seed("Scoped two", [{"title": "Scoped story two", "url": lab.publisher + "/two"}], parent, "/two")
    lab.seed("Outside", [{"title": "Outside story", "url": lab.publisher + "/outside"}], path="/outside")
    lab.show()
    p = lab.page
    p.locator(".folder-line").filter(has_text="Parent").locator("button").last.click()
    expect(p.locator(".article-row")).to_have_count(2)
    p.get_by_role("button", name="Mark this stream as read", exact=True).click()
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 1)
    p.keyboard.press("u")
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 3)
    p.locator(".feed-line").filter(has_text="Scoped one").click()
    p.get_by_role("button", name="Mark this stream as read", exact=True).click()
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 2)
    lab.open_title("Scoped story one")
    p.locator(".secondary-tools").get_by_role("button", name="Delete", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(0)
    p.keyboard.press("u")
    expect(p.locator(".article-row")).to_have_count(1)


@pytest.mark.features(4, 1)
def test_linkless_hash_entries_remain_distinct(lab):
    lab.routes["/linkless"] = (
        200,
        {"Content-Type": "application/rss+xml"},
        '<rss version="2.0"><channel><title>Linkless</title><description>Hash fallback</description><item><title>One without ID</title><description>First body</description></item><item><title>Two without ID</title><description>Second body</description></item></channel></rss>',
    )
    lab.show()
    lab.upload(
        f'<opml><body><outline text="Linkless" type="rss" xmlUrl="{lab.publisher}/linkless"/></body></opml>'
    )
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 1)
    lab.finish_jobs()
    lab.close()
    lab.page.reload()
    expect(lab.page.locator(".article-row")).to_have_count(2)
    lab.page.get_by_role("button", name="Refresh feeds (R)", exact=True).click()
    lab.finish_jobs()
    lab.page.reload()
    expect(lab.page.locator(".article-row")).to_have_count(2)


@pytest.mark.features(37, 39)
def test_multiple_audio_enclosures_keep_their_own_download(lab):
    first = lab.publisher + "/episode.wav"
    second = lab.publisher + "/second.wav"
    lab.routes["/second.wav"] = lab.routes["/episode.wav"]
    lab.seed(
        articles=[
            {
                "title": "Two audio tracks",
                "enclosures": [{"href": first, "type": "audio/wav"}, {"href": second, "type": "audio/wav"}],
            }
        ]
    )
    lab.show()
    lab.open_title("Two audio tracks")
    p = lab.page
    expect(p.locator("audio")).to_have_count(2)
    p.locator(".podcast").first.get_by_role("button", name="Download", exact=True).click()
    lab.finish_jobs()
    p.reload()
    lab.open_title("Two audio tracks")
    assert "/api/downloads/" in p.locator("audio").nth(0).get_attribute("src")
    assert p.locator("audio").nth(1).get_attribute("src") == second, (
        "Undownloaded track incorrectly plays the first track’s file"
    )


@pytest.mark.features(43, 51)
@pytest.mark.protected
def test_private_login_session_and_cross_origin_mutation(lab):
    p = lab.page
    p.goto(lab.base)
    expect(p.get_by_role("textbox", name="Access token")).to_be_visible()
    p.get_by_role("textbox", name="Access token").fill("wrong-token")
    p.get_by_role("button", name="Open Feedelio", exact=True).click()
    expect(p.get_by_role("alert")).to_contain_text("Incorrect access token")
    p.get_by_role("textbox", name="Access token").fill("isolated-audit-token")
    p.get_by_role("button", name="Open Feedelio", exact=True).click()
    expect(p.locator(".brand")).to_be_visible()
    assert "feedelio_session" not in p.evaluate("document.cookie")
    cookies = p.context.cookies(lab.base)
    assert next(c for c in cookies if c["name"] == "feedelio_session")["httpOnly"]
    lab.routes["/hostile"] = (200, {"Content-Type": "text/html"}, "<html><h1>Hostile origin</h1></html>")
    attacker = p.context.new_page()
    attacker.goto(lab.publisher + "/hostile")
    result = attacker.evaluate(
        """async base=>{try {let r=await fetch(base+'/api/actions/save_settings',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({payload:{values:{theme:'dark'}}})});return r.status;}catch{return 'blocked';}}""",
        lab.base,
    )
    assert result in ("blocked", 401, 403)
    assert lab.api("overview")["settings"]["theme"] == "system"
    attacker.close()


@pytest.mark.features(5, 18)
def test_worker_polls_due_feed_without_manual_refresh(lab):
    feed = lab.seed(articles=[{"title": "Initial story"}])
    lab.show()
    expect(lab.page.locator(".article-row")).to_have_count(1)
    lab.rss("/feed", "Audit feed", [{"title": "Initial story"}, {"title": "Automatically polled story"}])
    with lab.core.db:
        lab.core.db.execute(
            "UPDATE feeds SET next_check=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), feed)
        )
    lab.start_worker()
    expect(lab.page.locator(".article-row")).to_have_count(2, timeout=25000)
    assert any(r["path"] == "/feed" for r in lab.requests)
    assert datetime.fromisoformat(lab.api("overview")["feeds"][0]["next_check"]) > datetime.now(timezone.utc)


@pytest.mark.features(28, 40, 42)
def test_automatic_readability_and_provided_caption_track(lab):
    paragraphs = [
        "The observatory stands above a coastal village where generations of astronomers have studied the night sky. Its new telescope records faint signals from distant galaxies, providing evidence about how stars formed in the early universe.",
        "Researchers compared observations collected over several winters. They corrected for atmospheric distortion and checked their measurements against independent instruments. The resulting catalogue contains thousands of objects that were previously too faint to resolve.",
        "These measurements reveal surprising patterns of stellar formation. Several galaxies contain unusually dense clusters of young stars. Further observations will establish whether these structures persist as the galaxies evolve over billions of years.",
    ]
    lab.routes["/readability"] = (
        200,
        {"Content-Type": "text/html"},
        "<html><head><title>Observatory research</title></head><body><nav>Home | Subscribe | Advertising</nav><article><h1>Observatory research</h1>"
        + "".join("<p>" + text + "</p>" for text in paragraphs)
        + '<video><track kind="captions" src="/captions.vtt"></video></article><footer>Unrelated footer</footer></body></html>',
    )
    lab.routes["/captions.vtt"] = (
        200,
        {"Content-Type": "text/vtt"},
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nSpectroheliography reveals new details.\n",
    )
    lab.seed(
        articles=[
            {
                "title": "Readability summary",
                "url": lab.publisher + "/readability",
                "body": "<p>Summary only.</p>",
            }
        ]
    )
    lab.show()
    lab.open_title("Readability summary")
    lab.page.get_by_role("button", name="Fetch full text", exact=True).click()
    lab.finish_jobs()
    lab.page.reload()
    lab.open_title("Readability summary")
    expect(lab.page.locator(".prose")).to_contain_text("atmospheric distortion")
    expect(lab.page.locator(".prose")).not_to_contain_text("Unrelated footer")
    expect(lab.page.locator(".transcript")).to_contain_text("Spectroheliography")
    lab.page.get_by_role("textbox", name="Search articles").fill("Spectroheliography")
    expect(lab.page.locator(".article-row")).to_have_count(1)


@pytest.mark.features(38)
def test_configured_local_invidious_is_actually_loaded(lab):
    lab.routes["/embed/abcdefghijk"] = (
        200,
        {"Content-Type": "text/html"},
        "<html><body>Local privacy player</body></html>",
    )
    lab.seed(articles=[{"title": "Local video", "url": "https://www.youtube.com/watch?v=abcdefghijk"}])
    lab.show()
    lab.manager("Data & connections")
    lab.page.get_by_role("textbox", name=re.compile("^Invidious instance")).fill(lab.publisher)
    lab.page.get_by_role("button", name="Save settings", exact=True).click()
    lab.until(lambda: lab.api("overview")["settings"]["invidious"] == lab.publisher)
    lab.close()
    lab.open_title("Local video")
    lab.page.get_by_role("button", name=re.compile("Play video")).click()
    expect(lab.page.frame_locator("iframe").locator("body")).to_contain_text("Local privacy player")
