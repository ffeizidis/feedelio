import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import expect


def stamp(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def add_rule(lab, name, field, pattern, action, value=""):
    p = lab.page
    lab.manager("Rules")
    form = p.locator(".rule-form")
    for key, text in {"name": name, "pattern": pattern, "value": value}.items():
        form.locator(f"input[name={key}]").fill(text)
    form.locator("select[name=field]").select_option(field)
    form.locator("select[name=action]").select_option(action)
    form.get_by_role("button", name="Add rule", exact=True).click()
    expect(p.locator(".rule-row").filter(has_text=name)).to_be_visible()


@pytest.mark.features(1, 2, 3, 4, 5, 6, 7, 14, 19, 25)
def test_four_formats_opml_polling_conditional_get_and_bad_feed(lab):
    p, base = lab.page, lab.publisher
    lab.rss(
        "/rss",
        "RSS",
        [{"title": "RSS story"}, {"guid": "alias", "title": "RSS duplicate", "url": base + "/article/0"}],
        {"ETag": '"v1"', "Last-Modified": "Tue, 15 Sep 2026 10:00:00 GMT"},
    )
    lab.routes["/json"] = (
        200,
        {"Content-Type": "application/feed+json"},
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "JSON",
                "items": [{"id": "j", "title": "JSON story", "content_text": "JSON body"}],
            }
        ),
    )
    lab.routes["/atom"] = (
        200,
        {"Content-Type": "application/atom+xml"},
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><id>urn:feed</id><updated>2026-09-15T00:00:00Z</updated><entry><id>urn:entry</id><title>Atom story</title><updated>2026-09-15T00:00:00Z</updated><content>Atom body</content></entry></feed>',
    )
    lab.routes["/rdf"] = (
        200,
        {"Content-Type": "application/rdf+xml"},
        f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"><channel rdf:about="{base}/rdf"><title>RDF</title><link>{base}</link><description>RDF</description></channel><item rdf:about="{base}/rdfstory"><title>RDF story</title><link>{base}/rdfstory</link><description>RDF body</description></item></rdf:RDF>',
    )
    lab.routes["/broken"] = (200, {"Content-Type": "application/rss+xml"}, "<rss>malformed")
    xml = (
        '<opml version="2.0"><body><outline text="Parent"><outline text="Child">'
        + "".join(
            f'<outline type="rss" text="{name}" xmlUrl="{base}/{name.lower()}"/>'
            for name in ["RSS", "JSON", "Atom", "RDF", "Broken"]
        )
        + "</outline></outline></body></opml>"
    )
    lab.show()
    lab.upload(xml)
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 5)
    lab.finish_jobs()
    lab.close()
    p.reload()
    expect(p.locator(".article-row")).to_have_count(4)
    for name in ["RSS", "JSON", "Atom", "RDF"]:
        lab.open_title(name + " story")
        expect(p.locator(".prose")).to_be_visible()
    parent = next(f for f in lab.api("overview")["folders"] if f["name"] == "Parent")
    child = next(f for f in lab.api("overview")["folders"] if f["name"] == "Child")
    assert child["parent_id"] == parent["id"]
    assert next(f for f in lab.api("overview")["feeds"] if f["title"] == "Broken")["health"] == "error"
    p.get_by_role("button", name="Refresh feeds (R)", exact=True).click()
    lab.finish_jobs()
    headers = [r["headers"] for r in lab.requests if r["path"] == "/rss"][-1]
    assert headers.get("If-None-Match") == '"v1"'
    assert headers.get("If-Modified-Since") == "Tue, 15 Sep 2026 10:00:00 GMT"
    assert lab.api("articles")["total"] == 4


@pytest.mark.features(8, 9, 11, 12, 13, 26, 27, 44)
def test_read_save_undo_history_sort_and_statistics(lab):
    lab.seed(articles=[{"title": "Newest", "published": stamp(1)}, {"title": "Older", "published": stamp(2)}])
    lab.show()
    p = lab.page
    lab.open_title("Newest")
    p.keyboard.press("s")
    expect(p.locator(".article-tools").get_by_role("button", name="Saved", exact=True)).to_be_visible()
    p.keyboard.press("m")
    expect(p.locator(".article-tools").get_by_role("button", name="Unread", exact=True)).to_be_visible()
    p.keyboard.press("u")
    expect(p.locator(".article-tools").get_by_role("button", name="Read", exact=True)).to_be_visible()
    p.get_by_role("button", name="Mark this stream as read", exact=True).click()
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 0)
    p.keyboard.press("u")
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 1)
    p.locator(".views").get_by_role("button", name="History", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(1)
    p.locator(".views").get_by_role("button", name=re.compile("All articles")).click()
    p.get_by_role("button", name="Newest first", exact=True).click()
    expect(p.locator(".article-row h3").first).to_have_text("Older")
    lab.manager("Activity")
    expect(p.locator(".stat-cards")).to_contain_text("Saved for later")
    assert lab.api("statistics")["totals"]["saved"] == 1


@pytest.mark.features(8, 13)
def test_keyboard_follows_visible_folder_group_order(lab):
    a = lab.core.save_folder("Alpha")["id"]
    b = lab.core.save_folder("Beta")["id"]
    lab.seed(
        "Alpha feed",
        [
            {"title": "First visible", "published": stamp(1)},
            {"title": "Second visible", "published": stamp(3)},
        ],
        a,
    )
    lab.seed("Beta feed", [{"title": "Third visible", "published": stamp(2)}], b, "/beta")
    lab.show()
    p = lab.page
    assert p.locator(".article-row h3").all_text_contents() == [
        "First visible",
        "Second visible",
        "Third visible",
    ]
    lab.open_title("First visible")
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text("Second visible")


@pytest.mark.features(8, 9, 13)
def test_unread_keyboard_can_go_back_after_opening(lab):
    lab.seed(
        articles=[
            {"title": "First", "published": stamp(1)},
            {"title": "Second", "published": stamp(2)},
            {"title": "Third", "published": stamp(3)},
        ]
    )
    lab.show()
    p = lab.page
    p.get_by_role("button", name="Unread only", exact=True).click()
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text("First")
    expect(p.locator(".article-row")).to_have_count(2)
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text("Second")
    expect(p.locator(".article-row")).to_have_count(1)
    p.keyboard.press("k")
    expect(p.locator(".reading-content h1")).to_have_text("First")


@pytest.mark.features(10, 21, 22, 45)
def test_typography_theme_and_persistence_at_200_percent(lab):
    lab.seed(articles=[{"title": "Typography", "body": "<p>" + "readable prose " * 240 + "</p>"}])
    lab.show()
    p = lab.page
    lab.open_title("Typography")
    article = lab.api("articles/" + lab.api("articles")["items"][0]["id"])
    assert article["words"] == 480 and article["reading_minutes"] == 3
    p.get_by_role("button", name="Reading appearance", exact=True).click()
    p.get_by_role("button", name="Dark", exact=True).click()
    expect(p.locator("html")).to_have_attribute("data-theme", "dark")
    p.locator(".fonts button").nth(2).click()
    p.get_by_role("slider", name=re.compile("Text size")).focus()
    p.keyboard.press("End")
    lab.until(lambda: lab.api("overview")["settings"]["font_size"] == 32)
    p.get_by_role("slider", name=re.compile("Line length")).focus()
    p.keyboard.press("Home")
    lab.until(lambda: lab.api("overview")["settings"]["width"] == 45)
    p.get_by_role("slider", name=re.compile("Line spacing")).focus()
    p.keyboard.press("End")
    lab.until(lambda: lab.api("overview")["settings"]["line_height"] == 2.2)
    lab.close()
    p.reload()
    lab.open_title("Typography")
    expect(p.locator(".reading-content")).to_have_class(re.compile("font-mono"))
    assert p.locator(".prose").evaluate("(e)=>getComputedStyle(e).fontSize") == "32px"
    p.evaluate("document.documentElement.style.zoom='2'")
    expect(p.locator(".prose")).to_be_visible()
    p.evaluate("document.documentElement.style.zoom='1'")
    p.get_by_role("button", name="Reading appearance", exact=True).click()
    p.locator(".segmented").first.locator("button").first.click()
    p.emulate_media(color_scheme="dark")
    expect(p.locator("html")).to_have_attribute("data-theme", "system")
    assert p.locator("html").evaluate("(e)=>getComputedStyle(e).colorScheme") == "dark"


@pytest.mark.features(7, 14, 15, 24, 25, 26)
def test_nested_folder_cycle_bulk_rename_move_delete_undo(lab):
    parent = lab.core.save_folder("Parent")["id"]
    child = lab.core.save_folder("Child", parent)["id"]
    lab.seed("First feed", [{"title": "A story"}], child)
    lab.seed("Second feed", [{"title": "Another story", "url": lab.publisher + "/b"}], child, "/second")
    lab.show()
    p = lab.page
    lab.manager("Folders")
    form = p.locator(".folder-editor").filter(has=p.locator('input[value="Parent"]'))
    form.get_by_role("combobox").select_option(child)
    form.get_by_role("button", name="Save", exact=True).click()
    expect(p.locator(".manager-body .inline-error")).to_contain_text("ancestors")
    lab.manager("Subscriptions")
    p.get_by_role("button", name="Edit First feed", exact=True).click()
    p.get_by_role("textbox", name="Custom title", exact=True).fill("Renamed journal")
    p.get_by_role("button", name="Save feed settings", exact=True).click()
    expect(p.locator(".feed-info").filter(has_text="Renamed journal")).to_be_visible()
    p.get_by_role("checkbox", name="Select Renamed journal", exact=True).check()
    p.get_by_role("checkbox", name="Select Second feed", exact=True).check()
    p.get_by_role("combobox", name="Move selected feeds", exact=True).select_option("inbox")
    lab.until(lambda: all(f["folder_id"] == "inbox" for f in lab.api("overview")["feeds"]))
    p.get_by_role("textbox", name="Batch feed tags", exact=True).fill("research, reading")
    p.get_by_role("button", name="Set tags", exact=True).click()
    lab.until(lambda: all(f["tags"] == ["research", "reading"] for f in lab.api("overview")["feeds"]))
    p.locator(".bulk-bar").get_by_role("button", name="Delete", exact=True).click()
    expect(p.locator(".feed-table>div")).to_have_count(0)
    lab.close()
    p.keyboard.press("u")
    expect(p.locator(".article-row")).to_have_count(2)


@pytest.mark.features(17, 18, 23, 30, 47)
def test_discovery_request_options_and_permanent_migration(lab):
    p = lab.page
    base = lab.publisher
    lab.rss("/destination", "Discovered", [{"title": "Found through website"}])
    lab.routes["/old"] = (301, {"Location": "/destination"}, "")
    lab.routes["/home"] = (
        200,
        {"Content-Type": "text/html"},
        '<link rel="alternate" type="application/rss+xml" href="/old" title="Discovered feed">',
    )
    lab.show()
    p.locator(".subscriptions-heading").get_by_role("button", name="Add a subscription", exact=True).click()
    p.get_by_role("textbox", name="Website or feed URL").fill(base + "/home")
    p.get_by_role("button", name="Find feeds", exact=True).click()
    p.locator(".discovered button").first.click()
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 1)
    lab.manager("Subscriptions")
    p.get_by_role("button", name=re.compile("^Edit ")).click()
    p.get_by_role("spinbutton", name=re.compile("Refresh interval")).fill("17")
    p.get_by_role("textbox", name="Custom User-Agent", exact=True).fill("AdversarialUA/1")
    p.get_by_label(re.compile("^Cookie header")).fill("audit_session=fixture")
    p.get_by_role("button", name="Save feed settings", exact=True).click()
    lab.finish_jobs()
    lab.close()
    p.reload()
    expect(p.locator(".article-row")).to_have_count(1)
    feed = lab.api("overview")["feeds"][0]
    assert feed["url"] == base + "/destination" and feed["interval"] == 17
    headers = next(r["headers"] for r in lab.requests if r["path"] == "/old")
    assert headers["User-Agent"] == "AdversarialUA/1" and headers["Cookie"] == "audit_session=fixture"


@pytest.mark.features(17, 49)
def test_discovery_does_not_offer_html_as_a_feed(lab):
    lab.routes["/no-feed"] = (200, {"Content-Type": "text/html"}, "<html><h1>No feeds here</h1></html>")
    lab.show()
    p = lab.page
    p.locator(".subscriptions-heading").get_by_role("button", name="Add a subscription", exact=True).click()
    p.get_by_role("textbox", name="Website or feed URL").fill(lab.publisher + "/no-feed")
    p.get_by_role("button", name="Find feeds", exact=True).click()
    expect(p.locator(".discovered button")).to_have_count(0)
    expect(p.locator("dialog")).to_contain_text(re.compile("no feed|not found", re.I))


@pytest.mark.features(16, 29, 45)
def test_xss_tracking_stripping_and_no_image_requests_before_opt_in(lab):
    base = lab.publisher
    body = f'''<p>Clean prose.</p><script>window.owned=1</script><img src="{base}/pixel" width="1" height="1"><img src="{base}/picture.svg" onerror="window.owned=2"><iframe src="{base}/frame"></iframe><a href="{base}/link?utm_source=tracker&amp;ok=1">Clean link</a><a href="javascript:window.owned=3">Bad link</a>'''
    lab.seed(articles=[{"title": "Hostile article", "body": body}])
    lab.show()
    lab.open_title("Hostile article")
    p = lab.page
    p.wait_for_timeout(300)
    assert not p.evaluate("window.owned")
    assert not [r for r in lab.requests if r["path"] in ["/pixel", "/picture.svg", "/frame", "/favicon.ico"]]
    assert "utm_" not in p.locator(".prose a").first.get_attribute("href")
    assert not p.locator(".prose script, .prose iframe").count()
    p.get_by_role("button", name=re.compile("Images are paused")).click()
    lab.until(lambda: any(r["path"] == "/picture.svg" for r in lab.requests))
    assert not any(r["path"] == "/pixel" for r in lab.requests)
    p.get_by_role("button", name="Reading appearance", exact=True).click()
    p.get_by_role("checkbox", name=re.compile("Load article images")).click()
    lab.until(lambda: lab.api("overview")["settings"]["images"])
    lab.close()
    lab.until(lambda: any(r["path"] == "/favicon.ico" for r in lab.requests))


@pytest.mark.features(29, 45)
def test_css_sized_tracking_pixel_is_removed(lab):
    lab.seed(
        articles=[
            {
                "title": "CSS tracking pixel",
                "body": f'<p>Text</p><img src="{lab.publisher}/pixel" style="width:1px;height:1px">',
            }
        ]
    )
    lab.routes["/pixel"] = lab.routes["/picture.svg"]
    lab.show()
    lab.open_title("CSS tracking pixel")
    p = lab.page
    p.get_by_role("button", name="Reading appearance", exact=True).click()
    p.get_by_role("checkbox", name=re.compile("Load article images")).click()
    lab.until(lambda: lab.api("overview")["settings"]["images"])
    lab.close()
    p.wait_for_timeout(300)
    assert not any(r["path"] == "/pixel" for r in lab.requests), (
        "CSS 1x1 tracking image reached the publisher"
    )


@pytest.mark.features(20, 21, 28, 32, 33, 34, 35, 40, 42, 50)
def test_full_text_canonical_paywall_rewrite_transcript_search_and_obsidian(lab):
    base = lab.publisher
    lab.routes["/amp"] = (
        200,
        {"Content-Type": "text/html"},
        f'''<html><head><title>Full article</title><link rel="canonical" href="{base}/canonical?utm_source=amp"><script type="application/ld+json">{{"isAccessibleForFree":false}}</script></head><body><main class="article-main"><h1>Expanded article</h1><p>Quasars and zebrafish explain an unusual universe. A forbiddenword is rewritten.</p></main><section id="transcript">Supplied transcript mentions magnetospheres.</section></body></html>''',
    )
    lab.seed(
        articles=[{"title": "Summary article", "url": base + "/amp", "body": "<p>Short feed summary.</p>"}]
    )
    lab.show()
    p = lab.page
    add_rule(lab, "Rewrite body", "body", "forbiddenword", "rewrite", "replacement")
    p.get_by_role("button", name="+ Mark paywalls as read", exact=True).click()
    lab.manager("Subscriptions")
    p.get_by_role("button", name="Edit Audit feed", exact=True).click()
    p.get_by_role("textbox", name="Full article CSS selector", exact=True).fill(".article-main")
    p.get_by_role("textbox", name="Provided transcript CSS selector", exact=True).fill("#transcript")
    p.get_by_role("button", name="Save feed settings", exact=True).click()
    lab.close()
    lab.open_title("Summary article")
    p.get_by_role("button", name="Fetch full text", exact=True).click()
    lab.finish_jobs()
    p.reload()
    lab.open_title("Summary article")
    expect(p.locator(".prose")).to_contain_text("replacement")
    expect(p.locator(".transcript")).to_contain_text("magnetospheres")
    assert p.locator(".article-tools a").get_attribute("href") == base + "/canonical"
    assert "paywall" in lab.api("articles/" + lab.api("articles")["items"][0]["id"])["tags"]
    p.get_by_role("textbox", name="Search articles").fill("magnetospheres")
    expect(p.locator(".article-row")).to_have_count(1)
    with p.expect_download() as download:
        p.get_by_role("link", name="Obsidian / Markdown", exact=True).click()
    text = __import__("pathlib").Path(download.value.path()).read_text()
    assert "magnetospheres" in text and "replacement" in text and "source:" in text


@pytest.mark.features(31, 11, 26)
def test_duplicate_recovers_when_original_feed_is_deleted(lab):
    title = "Identical scientific discovery across several publications"
    lab.seed("Original", [{"title": title}], path="/original")
    lab.seed("Duplicate", [{"title": title, "url": lab.publisher + "/other"}], path="/duplicate")
    lab.show()
    p = lab.page
    expect(p.locator(".article-row")).to_have_count(1)
    lab.manager("Subscriptions")
    p.get_by_role("checkbox", name="Select Original", exact=True).check()
    p.locator(".bulk-bar").get_by_role("button", name="Delete", exact=True).click()
    lab.close()
    expect(p.locator(".article-row")).to_have_count(1)
    expect(p.locator(".row-meta .source")).to_have_text("Duplicate")


@pytest.mark.features(11, 31, 32, 34)
def test_rules_star_tag_drop_short_and_timeout(lab):
    lab.seed(
        articles=[
            {"title": "Save this item"},
            {"title": "Drop this advertisement", "url": lab.publisher + "/ad"},
            {"title": "A short", "url": "https://www.youtube.com/shorts/abcdefghijk"},
        ]
    )
    lab.show()
    p = lab.page
    add_rule(lab, "Star it", "title", "Save", "star")
    add_rule(lab, "Tag it", "title", "Save", "tag", "keeper")
    add_rule(lab, "Drop ads", "title", "advertisement", "drop")
    p.get_by_role("button", name="+ Skip YouTube Shorts", exact=True).click()
    p.get_by_role("button", name="Apply rules to existing articles", exact=True).click()
    lab.finish_jobs()
    lab.close()
    p.reload()
    expect(p.locator(".article-row")).to_have_count(2)
    stored = lab.api("articles")["items"]
    saved = next(a for a in stored if a["title"] == "Save this item")
    assert saved["starred"] and saved["tags"] == ["keeper"]
    assert next(a for a in stored if a["title"] == "A short")["read"]


@pytest.mark.features(36, 38, 45)
def test_video_is_click_to_load_and_uses_configured_invidious(lab):
    lab.seed(articles=[{"title": "Video episode", "url": "https://www.youtube.com/watch?v=abcdefghijk"}])
    lab.show()
    p = lab.page
    lab.open_title("Video episode")
    external = []
    p.on("request", lambda r: external.append(r.url) if "youtube" in r.url else None)
    assert p.locator("iframe").count() == 0
    p.get_by_role("button", name=re.compile("Play video")).click()
    expect(p.locator("iframe")).to_have_attribute("src", "https://www.youtube-nocookie.com/embed/abcdefghijk")
    lab.manager("Data & connections")
    p.get_by_role("textbox", name=re.compile("^Invidious instance")).fill(lab.publisher)
    p.get_by_role("button", name="Save settings", exact=True).click()
    lab.close()
    expect(p.locator("iframe")).to_have_attribute("src", lab.publisher + "/embed/abcdefghijk")


@pytest.mark.features(37, 39, 40, 42, 43)
def test_real_audio_download_offline_playback_and_full_backup(lab):
    base = lab.publisher
    lab.rss(
        "/podcast",
        "Podcast",
        [
            {
                "title": "Podcast episode",
                "extra": f'<enclosure url="{base}/episode.wav" type="audio/wav"/><podcast:transcript url="{base}/transcript" type="text/plain"/>',
            }
        ],
    )
    lab.routes["/transcript"] = (
        200,
        {"Content-Type": "text/plain"},
        "Provided podcast transcript about xenobiology.",
    )
    lab.show()
    lab.upload(f'<opml><body><outline type="rss" text="Podcast" xmlUrl="{base}/podcast"/></body></opml>')
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 1)
    lab.finish_jobs()
    lab.close()
    lab.page.reload()
    lab.open_title("Podcast episode")
    p = lab.page
    expect(p.locator("audio")).to_have_attribute("preload", "none")
    expect(p.locator(".transcript")).to_contain_text("xenobiology")
    p.locator(".podcast").get_by_role("button", name="Download", exact=True).click()
    lab.finish_jobs()
    p.reload()
    lab.open_title("Podcast episode")
    lab.routes["/episode.wav"] = (503, {}, "Offline publisher")
    assert "/api/downloads/" in p.locator("audio").get_attribute("src")
    assert p.locator("audio").evaluate("async e=>{await e.play();return !e.paused}")
    lab.manager("Data & connections")
    with p.expect_download() as dl:
        p.get_by_role("link", name="Full JSON backup", exact=True).click()
    backup = json.loads(__import__("pathlib").Path(dl.value.path()).read_text())
    assert len(backup["media"]) == 1 and "xenobiology" in json.dumps(backup)
    lab.manager("Downloads")
    p.get_by_role("button", name="Delete downloaded episode", exact=True).click()
    expect(p.locator(".download-row")).to_contain_text("removed")


@pytest.mark.features(6, 43)
def test_malformed_opml_is_atomic(lab):
    lab.rss("/valid", "Valid", [])
    lab.show()
    lab.upload(
        f'<opml><body><outline type="rss" text="Valid" xmlUrl="{lab.publisher}/valid"/><outline type="rss" text="Invalid" xmlUrl="file:///etc/passwd"/></body></opml>'
    )
    expect(lab.page.locator(".manager-body .inline-error")).to_be_visible()
    assert len(lab.api("overview")["feeds"]) == 0, "Rejected OPML partially imported subscriptions"


@pytest.mark.features(41, 46, 24)
def test_retention_and_archive_backfill_through_ui(lab):
    lab.seed(
        articles=[
            {"title": "Old disposable"},
            {"title": "Old saved", "url": lab.publisher + "/saved"},
            {"title": "Old unread", "url": lab.publisher + "/unread"},
        ]
    )
    with lab.core.db:
        lab.core.db.execute("UPDATE articles SET added=?", (stamp(24 * 200),))
    lab.show()
    p = lab.page
    lab.open_title("Old saved")
    p.keyboard.press("s")
    lab.until(lambda: lab.api("overview")["counts"]["starred"] == 1)
    lab.open_title("Old disposable")
    lab.manager("Subscriptions")
    p.get_by_role("button", name="Edit Audit feed", exact=True).click()
    p.get_by_role("checkbox", name="Keep this feed’s archive forever", exact=True).check()
    lab.routes["/archive"] = (
        200,
        {"Content-Type": "application/feed+json"},
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "Archive",
                "items": [
                    {
                        "id": "past",
                        "title": "Backfilled historical story",
                        "url": lab.publisher + "/past",
                        "content_text": "History",
                    }
                ],
            }
        ),
    )
    p.get_by_role("textbox", name="Archive feed URL", exact=True).fill(lab.publisher + "/archive")
    p.get_by_role("button", name="Save feed settings", exact=True).click()
    p.get_by_role("button", name="Edit Audit feed", exact=True).click()
    p.get_by_role("button", name="Backfill archive", exact=True).click()
    lab.finish_jobs()
    lab.close()
    p.reload()
    expect(p.locator(".article-row")).to_have_count(4)
    lab.command("enqueue", {"kind": "purge"})
    lab.finish_jobs()
    p.reload()
    expect(p.locator(".article-row")).to_have_count(4)
    lab.manager("Subscriptions")
    p.get_by_role("button", name="Edit Audit feed", exact=True).click()
    p.get_by_role("checkbox", name="Keep this feed’s archive forever", exact=True).uncheck()
    p.get_by_role("button", name="Save feed settings", exact=True).click()
    lab.close()
    # The scheduled nightly job has no UI trigger; enqueue through the browser's authenticated API.
    lab.command("enqueue", {"kind": "purge"})
    lab.finish_jobs()
    p.reload()
    expect(p.locator(".article-row")).to_have_count(3)
    assert "Old disposable" not in p.locator(".article-row h3").all_text_contents()


@pytest.mark.features(48, 49, 33)
def test_rsshub_route_and_css_site_scraper_from_add_dialog(lab):
    lab.rss("/bridge/example", "Bridge", [{"title": "External bridge story"}])
    lab.routes["/site"] = (
        200,
        {"Content-Type": "text/html"},
        '<article class="story"><a href="/story-a">Scraped story A</a></article><article class="story"><a href="/story-b">Scraped story B</a></article>',
    )
    lab.show()
    p = lab.page
    lab.manager("Data & connections")
    p.get_by_role("textbox", name="RSSHub instance", exact=True).fill(lab.publisher)
    p.get_by_role("button", name="Save settings", exact=True).click()
    lab.close()
    p.locator(".subscriptions-heading").get_by_role("button", name="Add a subscription", exact=True).click()
    p.get_by_role("button", name="Advanced", exact=True).click()
    p.get_by_role("textbox", name=re.compile("^RSSHub route")).fill("/bridge/example")
    p.get_by_role("button", name="Subscribe through RSSHub", exact=True).click()
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 1)
    p.locator(".subscriptions-heading").get_by_role("button", name="Add a subscription", exact=True).click()
    p.get_by_role("button", name="Advanced", exact=True).click()
    p.get_by_role("textbox", name="Website or feed URL").fill(lab.publisher + "/site")
    p.get_by_role("textbox", name=re.compile("^No feed")).fill("article.story")
    p.get_by_role("button", name="Subscribe with site scraper", exact=True).click()
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 2)
    lab.finish_jobs()
    p.reload()
    expect(p.locator(".article-row")).to_have_count(3)


@pytest.mark.features(20, 26, 32, 43)
def test_hostile_search_bad_rules_and_backend_remains_responsive(lab):
    lab.seed(
        articles=[
            {"title": "Safe searchable article", "body": "<p>Unicode καλημέρα 🌿 searchable material</p>"}
        ]
    )
    lab.show()
    p = lab.page
    for query in ['" OR title:* --', "'; DROP TABLE articles;--", "καλημέρα", "<script>alert(1)</script>"]:
        p.get_by_role("textbox", name="Search articles").fill(query)
        lab.until(lambda: not p.locator(".article-list .spin").count())
        assert lab.api("overview")["counts"]["total"] == 1
    p.get_by_role("textbox", name="Search articles").fill("")
    lab.manager("Rules")
    form = p.locator(".rule-form")
    form.locator("input[name=name]").fill("Invalid regex")
    form.locator("input[name=pattern]").fill("[")
    form.get_by_role("button", name="Add rule", exact=True).click()
    expect(p.locator(".manager-body .inline-error")).to_be_visible()
    assert not lab.api("rules")
    assert lab.api("overview")["counts"]["total"] == 1
