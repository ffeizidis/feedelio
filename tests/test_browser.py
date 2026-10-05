"""Desktop acceptance test against the built React app and the real API."""

import base64
import json
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from feedelio.api import app
from feedelio.core import now


@pytest.fixture
def web_server(core):
    if not Path("web/dist/index.html").exists():
        pytest.skip("Build the frontend with npm run build --prefix web first.")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)
    sock.close()


def pick(page, label, option):
    """Choose an option from one of the app's dropdowns."""
    page.get_by_role("combobox", name=label).click()
    page.get_by_role("listbox", name=label).get_by_role("option", name=option, exact=True).click()


def test_desktop_reading_workflow(core, site, web_server):
    base, _, _ = site
    folder = core.save_folder("Ideas & culture")["id"]
    feed = core.subscribe(base + "/rss", folder, "The Sunday Journal")["id"]
    core.refresh(feed)
    core.ingest(
        feed,
        "second",
        "Finding beauty in the everyday",
        base + "/second",
        "<p>There is a kind of attention that asks nothing of the world except that it be itself.</p><h2>The art of noticing</h2><p>A walk without a destination can change the shape of an afternoon.</p>",
    )
    core.ingest(
        feed,
        "short",
        "A sixty-second essay",
        "https://www.youtube.com/shorts/abcdefghijk",
        "Watch it",
        published="2020-01-01T00:00:00+00:00",
        tags=["paywall"],
    )
    core.db.execute("UPDATE articles SET duration=60 WHERE guid='short'")
    core.db.commit()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(web_server)
        expect(page.locator(".stream-pane .article-list")).to_be_visible()
        expect(page.locator(".article-row")).to_have_count(3)
        marks = page.locator(".article-row", has_text="A sixty-second essay").locator(".row-marks")
        expect(marks.get_by_label("YouTube Short")).to_be_visible()
        expect(marks.get_by_label("Paywalled")).to_be_visible()
        page.keyboard.press("j")
        expect(page.locator(".reading-content h1")).to_be_visible()
        page.keyboard.press("s")
        expect(page.get_by_role("button", name="Unsave article", exact=True)).to_be_visible()
        page.keyboard.press("/")
        expect(page.get_by_role("textbox", name="Search articles")).to_be_focused()
        page.get_by_role("textbox", name="Search articles").fill("independent")
        expect(page.locator(".article-row")).to_have_count(1)
        page.locator(".row-title").click()
        expect(page.locator(".reading-content h1")).to_have_text("A slower and more thoughtful internet")
        page.get_by_role("button", name="esc to close", exact=True).click()
        page.locator(".article-row", has_text="A sixty-second essay").locator(".row-title").click()
        expect(page.locator(".article-details summary")).to_have_text("1:00 video")
        expect(page).to_have_title("feedelio - A sixty-second essay")
        expect(page.locator(".article-kicker")).to_contain_text("The Sunday Journal·January 1, 2020")
        expect(
            page.locator(".article-row", has_text="A sixty-second essay").locator(".row-date")
        ).to_have_text("6y")
        expect(page.locator(".video-box iframe")).to_have_attribute(  # No click-to-load step.
            "src", "https://www.youtube-nocookie.com/embed/abcdefghijk"
        )
        page.keyboard.press("Escape")
        expect(page.locator(".video-box")).to_have_count(0)
        page.get_by_role("button", name="Manage library", exact=True).click()
        page.get_by_role("button", name="Appearance", exact=True).click()
        page.get_by_role("button", name="Dark", exact=True).click()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        page.get_by_role("button", name="Light", exact=True).click()
        page.get_by_role("button", name="Close library").click()
        output = Path("test-results")
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / "desktop.png"), full_page=True)
        page.get_by_role("button", name="Manage library").click()
        expect(page).to_have_url(web_server + "/library/feeds")
        page.get_by_role("button", name="Rules", exact=True).click()
        expect(page).to_have_url(web_server + "/library/rules")
        page.reload()  # The library and its section come back by address.
        expect(page.get_by_role("heading", name="Rules", exact=True)).to_be_visible()
        page.get_by_role("button", name="Feeds", exact=True).click()
        # A feed opened from the list is a layer of its own: Back returns to the list.
        pick(page, "Filter feeds by status", "Failing (0)")
        expect(page.locator(".feed-table .feed-info")).to_have_count(0)
        pick(page, "Filter feeds by status", "All feeds")
        expect(page.locator(".feed-info a", has_text=base)).to_have_attribute("href", base)
        page.locator(".feed-open", has_text="The Sunday Journal").click()
        expect(page.get_by_role("textbox", name="Archive feed URL", exact=True)).to_be_visible()
        expect(page).to_have_url(web_server + "/library/feeds/" + feed)
        expect(page.locator(".feed-links a").first).to_have_attribute("href", base + "/rss")
        expect(page.locator(".feed-links a").nth(1)).to_have_attribute("href", base)
        page.go_back()
        expect(page.locator(".feed-info", has_text="The Sunday Journal")).to_be_visible()
        expect(page).to_have_url(web_server + "/library/feeds")
        page.get_by_role("button", name="Folders", exact=True).click()
        page.get_by_role("button", name="+ New folder", exact=True).click()
        page.get_by_role("textbox", name="Folder name").fill("Engineering")
        page.get_by_role("button", name="Create folder").click()
        expect(page.locator(".folder-group-heading").filter(has_text="Engineering")).to_be_visible()
        # The parent picker can create a folder inline; duplicate names are refused.
        page.get_by_role("button", name="+ New folder", exact=True).click()
        page.get_by_role("textbox", name="Folder name").fill("engineering")
        page.get_by_role("button", name="Create folder").click()
        expect(page.locator("dialog p[role=alert]")).to_contain_text("already exists")
        page.get_by_role("textbox", name="Folder name").fill("Compilers")
        pick(page, "Put folder under", "+ New folder…")
        page.get_by_role("textbox", name="New folder name").fill("Computing")
        page.get_by_role("button", name="Create", exact=True).click()
        expect(page.get_by_role("combobox", name="Put folder under")).to_have_text("Computing")
        page.get_by_role("button", name="Create folder").click()
        # Folders start collapsed; the same name is fine under a different parent.
        expect(page.locator(".folder-group-heading", has_text="Compilers")).to_have_count(0)
        page.get_by_role("dialog", name="Manage library").get_by_role(
            "button", name="Expand Computing"
        ).click()
        page.get_by_role("button", name="+ New folder", exact=True).click()
        page.get_by_role("textbox", name="Folder name").fill("Engineering")
        # The picker lists the tree: a folder's children sit indented under it.
        page.get_by_role("combobox", name="Put folder under").click()
        options = page.get_by_role("listbox", name="Put folder under").get_by_role("option")
        expect(options.nth(1)).to_have_text("Computing")
        expect(options.nth(2)).to_have_text("Compilers")
        assert int(options.nth(2).evaluate("e => parseInt(e.style.paddingLeft)")) > int(
            options.nth(1).evaluate("e => parseInt(e.style.paddingLeft)")
        )
        # A picker stays open until an option is chosen: the mouse button
        # coming up over it, or a refresh re-rendering the page, does not close it.
        page.mouse.up()
        page.mouse.wheel(0, 200)
        page.wait_for_timeout(300)
        expect(options.nth(1)).to_be_visible()
        options.nth(1).click()
        expect(page.get_by_role("listbox", name="Put folder under")).to_have_count(0)
        page.get_by_role("button", name="Create folder").click()
        expect(page.locator(".folder-group-heading", has_text="Engineering")).to_have_count(2)
        nested = page.locator(".folder-group", has_text="Computing").locator(
            ".folder-nested .folder-group-heading"
        )
        expect(nested).to_have_count(2)
        expect(nested.first).to_contain_text("Compilers")
        # Folders nest by drag and drop too, and drop back to the top level.
        page.locator(".folder-group-heading", has_text="Compilers").drag_to(
            nested.filter(has_text="Engineering")
        )
        expect(nested).to_have_count(1)
        nested.get_by_role("button", name="Expand Engineering").click()
        expect(nested).to_have_count(2)
        page.locator(".folder-group-heading", has_text="Compilers").drag_to(page.locator(".folder-drop-hint"))
        expect(
            page.locator(".folder-group", has_text="Computing").locator(
                ".folder-nested .folder-group-heading"
            )
        ).to_have_count(1)
        page.get_by_role("button", name="Rules", exact=True).click()
        page.get_by_role("button", name="+ Skip YouTube Shorts", exact=True).click()
        expect(page.locator(".rule-row")).to_have_count(1)
        page.get_by_role("button", name="Import & export", exact=True).click()
        expect(page.get_by_role("link", name="Full JSON backup")).to_be_visible()
        page.get_by_role("button", name="Close library").click()
        expect(page).to_have_url(web_server + "/")
        page.keyboard.press("?")
        expect(page.get_by_role("heading", name="Keyboard", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("dialog")).to_have_count(0)
        # Browser Back (also a mouse side button) closes the open layer instead of leaving.
        page.locator(".article-row .row-title").first.click()
        expect(page.locator(".reading-content h1")).to_be_visible()
        page.go_back()
        expect(page.locator(".reading-content h1")).to_have_count(0)
        assert page.url.startswith(web_server)
        page.locator(".feed-line", has_text="The Sunday Journal").click()
        expect(page).to_have_title("feedelio - The Sunday Journal")
        # Every page has an address, so a reload lands where the reader was.
        expect(page).to_have_url(web_server + "/f/" + feed)
        page.reload()
        expect(page).to_have_title("feedelio - The Sunday Journal")
        page.locator(".article-row .row-title").first.click()
        expect(page).to_have_url(re.compile(r"/a/[0-9a-f]+$"))
        page.go_back()
        expect(page).to_have_url(web_server + "/f/" + feed)
        expect(page.locator(".reading-content h1")).to_have_count(0)
        page.locator(".article-row .row-title").first.click()
        page.reload()
        expect(page.locator(".reading-content h1")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator(".reading-content h1")).to_have_count(0)
        expect(page.locator(".subscriptions-heading h2")).to_have_text("FEEDS")
        page.get_by_role("button", name="Bookmark this feed").click()
        expect(page.get_by_role("button", name="Remove bookmark")).to_be_visible()
        page.get_by_role("button", name="Bookmarked feeds").click()
        expect(page).to_have_title("feedelio - Bookmarked feeds")
        expect(page.locator(".article-row")).to_have_count(3)
        page.locator(".feed-line", has_text="The Sunday Journal").click()
        # Update now queues one refresh and stays busy until the worker takes it
        # (the subscription's own initial refresh is still queued: no worker here).
        expect(page.get_by_role("button", name="Updating…")).to_be_disabled()
        core.db.execute("UPDATE jobs SET status='done' WHERE kind='refresh'")
        core.db.commit()
        page.get_by_role("button", name="Update now", exact=True).click()
        expect(page.get_by_role("button", name="Updating…")).to_be_disabled()
        assert core.rows("SELECT count(*) c FROM jobs WHERE kind='refresh' AND status='queued'")[0]["c"] == 1
        core.enqueue("backfill", {"feed_id": feed})
        expect(page.get_by_role("button", name="Archive progress")).to_contain_text(
            "Archive in progress · 0 pages · 0 older articles", timeout=15000
        )  # The overview polls every ten seconds.
        page.get_by_role("button", name="Manage feed: The Sunday Journal").click()
        expect(page.get_by_role("heading", name="Manage library")).to_be_visible()
        expect(page.get_by_role("textbox", name="Archive feed URL", exact=True)).to_be_visible()
        expect(page.locator(".feed-check")).to_contain_text("Last checked")
        expect(page.locator(".feed-check button")).to_be_disabled()  # Still queued.
        page.get_by_role("button", name="Downloads", exact=True).click()
        log = page.locator("details.jobs-log")
        expect(log.locator(".job-row").first).to_be_hidden()  # Collapsed until opened.
        log.locator("summary").click()
        expect(log.locator(".job-row").first.locator("time")).to_have_attribute(
            "datetime", re.compile(r"^\d{4}-")
        )
        expect(log.locator(".job-row", has_text="backfill The Sunday Journal")).to_be_visible()
        page.get_by_role("button", name="Activity", exact=True).click()
        expect(page.get_by_text("Archive size · 3 articles")).to_be_visible()
        page.go_back()
        expect(page.locator("dialog")).to_have_count(0)
        assert page.url.startswith(web_server)
        # Only feeds with unread articles, once everything here is read.
        expect(page.locator(".feed-line")).to_have_count(1)
        page.get_by_role("button", name="Show only feeds with unread articles").click()
        expect(page.get_by_role("button", name="Show all feeds")).to_be_visible()
        expect(page.locator(".feed-line")).to_have_count(1)
        page.locator(".feed-line", has_text="The Sunday Journal").click()
        page.get_by_role("button", name="Mark this stream as read").click()
        expect(page.locator(".feed-line")).to_have_count(1)  # The feed being read stays.
        page.get_by_role("button", name="All articles").click()
        expect(page.locator(".feed-line")).to_have_count(0)
        page.get_by_role("button", name="Show all feeds").click()
        expect(page.locator(".feed-line")).to_have_count(1)
        # A folder's heading opens that folder's settings; a collapsed folder stays
        # collapsed across visits; library sections are listed by name.
        page.get_by_role("button", name="Ideas & culture", exact=True).click()
        page.get_by_role("button", name="Manage folder: Ideas & culture").click()
        expect(page.get_by_role("textbox", name="Folder name")).to_have_value("Ideas & culture")
        expect(page).to_have_url(web_server + "/library/folders/" + folder)
        expect(page.locator(".dialog-tabs button").first).to_have_text("Activity")
        page.get_by_role("button", name="Close dialog").click()
        expect(page).to_have_url(web_server + "/library/folders")
        page.get_by_role("button", name="Close library").click()
        page.get_by_role("button", name="Collapse Ideas & culture").click()
        expect(page.locator(".feed-line")).to_have_count(0)
        page.reload()
        expect(page.get_by_role("button", name="Expand Ideas & culture")).to_be_visible()
        page.get_by_role("button", name="Expand Ideas & culture").click()
        expect(page.locator(".feed-line")).to_have_count(1)
        # Desktop-only shell keeps the tray and three panes at 200% zoom, with
        # horizontal scrolling.
        page.locator(".article-row .row-title").first.click()
        page.evaluate("document.documentElement.style.zoom='2'")
        expect(page.locator(".prose")).to_be_visible()
        assert (
            page.locator(".shell").evaluate(
                '(el)=>getComputedStyle(el).gridTemplateColumns.split(" ").length'
            )
            == 4
        )
        assert not errors, errors
        browser.close()


def test_rules_are_edited_previewed_and_applied(core, site, web_server):
    """The rules tab: a gated form, an edit that keeps its place, an exact dry run."""
    base, _, _ = site
    feed = core.subscribe(base + "/rss", "inbox", "The Sunday Journal")["id"]
    for i, title in enumerate(["An advert here", "A keeper", "Plain reading"]):
        core.ingest(feed, str(i), title, f"{base}/a{i}", "<p>Ordinary body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(web_server)
        page.get_by_role("button", name="Manage library").click()
        page.get_by_role("button", name="Rules", exact=True).click()

        # The fields stay out of the way until they are asked for.
        expect(page.locator(".rule-form")).to_have_count(0)
        page.get_by_role("button", name="+ Add new rule", exact=True).click()
        form = page.locator(".rule-form")
        expect(form).to_be_visible()

        # Regex syntax is a hover away, and only while the pattern is a regex.
        help = form.locator(".regex-help")
        expect(page.get_by_role("tooltip")).to_be_hidden()
        help.hover()
        expect(page.get_by_role("tooltip")).to_contain_text("word boundary")
        form.get_by_role("switch", name="Regular expression").click()
        expect(help).to_have_count(0)
        form.get_by_role("switch", name="Regular expression").click()

        # A pattern the engine cannot compile is refused before it can be saved.
        form.locator("input[name=name]").fill("Skip adverts")
        form.locator("textarea[name=pattern]").fill("[")
        expect(page.locator(".rule-line-error")).to_contain_text("Line 1")
        expect(form.get_by_role("button", name="Add rule", exact=True)).to_be_disabled()

        # One rule, two patterns, either of which fires it.
        form.locator("textarea[name=pattern]").fill("advert\nkeeper")
        expect(page.locator(".rule-line-error")).to_have_count(0)

        # Counted before it is saved, so a pattern can be tried on the real library.
        form.get_by_role("button", name="Preview this rule", exact=True).click()
        expect(form.locator(".rule-preview")).to_contain_text("2 of 3 stored articles")

        # A valid pattern that matches nothing says so rather than passing quietly.
        form.locator("textarea[name=pattern]").fill("^ An advert here ")
        expect(form.locator(".rule-preview")).to_have_count(0)  # The count was for the old pattern.
        expect(page.locator(".rule-line-error")).to_have_count(0)
        form.get_by_role("button", name="Preview this rule", exact=True).click()
        expect(form.locator(".rule-preview")).to_contain_text("0 of 3 stored articles")
        expect(form.locator(".rule-line-warning")).to_contain_text("stray spaces")

        form.locator("textarea[name=pattern]").fill("advert\nkeeper")
        form.get_by_role("button", name="Add rule", exact=True).click()
        expect(page.locator(".rule-row")).to_have_count(1)
        expect(page.locator(".rule-form")).to_have_count(0)  # It closes behind itself.

        # A second rule, so the edit below has an order to preserve.
        page.get_by_role("button", name="+ Add new rule", exact=True).click()
        page.locator(".rule-form input[name=name]").fill("Star keepers")
        page.locator(".rule-form textarea[name=pattern]").fill("keeper")
        pick(page, "Action", "star")
        page.locator(".rule-form").get_by_role("button", name="Add rule", exact=True).click()
        expect(page.locator(".rule-row")).to_have_count(2)

        # Editing reopens the same form, filled in, and the rule keeps its place.
        page.get_by_role("button", name="Edit Skip adverts", exact=True).click()
        expect(page.locator(".rule-form input[name=name]")).to_have_value("Skip adverts")
        expect(page.locator(".rule-form textarea[name=pattern]")).to_have_value("advert\nkeeper")
        page.locator(".rule-form input[name=name]").fill("Skip the adverts")
        page.locator(".rule-form").get_by_role("button", name="Save rule", exact=True).click()
        expect(page.locator(".rule-row").first).to_contain_text("Skip the adverts")
        expect(page.locator(".rule-row")).to_have_count(2)

        # The dry run says exactly what applying would do, and applying does it.
        page.get_by_role("button", name="Preview changes", exact=True).click()
        expect(page.locator(".rule-preview")).to_contain_text("2 of 3 stored articles")
        page.get_by_role("button", name="Apply these changes", exact=True).click()
        expect(page.locator(".rule-preview")).to_have_count(0)

        # Deleting asks first; dismissing it keeps the rule.
        page.once("dialog", lambda d: d.dismiss())
        page.get_by_role("button", name="Delete Star keepers", exact=True).click()
        expect(page.locator(".rule-row")).to_have_count(2)
        page.once("dialog", lambda d: d.accept())
        page.get_by_role("button", name="Delete Star keepers", exact=True).click()
        expect(page.locator(".rule-row")).to_have_count(1)
        browser.close()


def test_a_long_dropdown_is_narrowed_by_typing(core, site, web_server):
    """A rule's scope among many feeds: type to search, rather than hunt."""
    base, routes, _ = site
    feed = routes["/rss"]
    for i in range(11):
        routes[f"/rss{i}"] = feed
        core.subscribe(f"{base}/rss{i}", "inbox", f"Journal number {i}")
    routes["/garden"] = feed
    core.subscribe(base + "/garden", "inbox", "The Gardening Weekly")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(web_server)
        page.get_by_role("button", name="Manage library").click()
        page.get_by_role("button", name="Rules", exact=True).click()
        page.get_by_role("button", name="+ Add new rule", exact=True).click()

        # A short dropdown is quicker to read than to filter, so it has no field.
        page.get_by_role("combobox", name="Action").click()
        expect(page.get_by_role("textbox", name="Search Action")).to_have_count(0)
        page.keyboard.press("Escape")

        # Twelve feeds is a hunt, so this one opens ready to be typed into.
        page.get_by_role("combobox", name="Scope").click()
        search = page.get_by_role("textbox", name="Search Scope")
        expect(search).to_be_focused()
        options = page.get_by_role("listbox", name="Scope")
        expect(options.get_by_role("option")).to_have_count(13)  # All feeds, plus the twelve.

        search.fill("garden")
        expect(options.get_by_role("option")).to_have_count(1)
        search.fill("number 1")  # A space belongs to the query, not to picking.
        expect(options.get_by_role("option")).to_have_count(2)  # 1 and 10.
        search.fill("nothing here")
        expect(options.get_by_role("option")).to_have_count(0)
        expect(page.locator(".select-empty")).to_be_visible()

        # Enter takes the first of what is left, and the choice sticks.
        search.fill("Gardening")
        page.keyboard.press("Enter")
        expect(page.get_by_role("combobox", name="Scope")).to_have_text("The Gardening Weekly")
        expect(page.get_by_role("listbox", name="Scope")).to_have_count(0)

        # Reopening starts from the whole list again, not the last search.
        page.get_by_role("combobox", name="Scope").click()
        expect(page.get_by_role("textbox", name="Search Scope")).to_have_value("")
        expect(page.get_by_role("listbox", name="Scope").get_by_role("option")).to_have_count(13)
        browser.close()


def test_a_clipped_title_shows_in_full_on_hover(core, site, web_server):
    """A pane too narrow for a name hides the rest of it; hovering gets it back."""
    base, _, _ = site
    long_title = "An unusually expansive headline about the quiet persistence of ordinary afternoons"
    long_feed = "The Exceptionally Long-Winded Quarterly Review of Gardening and Related Pastimes"
    long_folder = "Essays, criticism and other things worth a slow read"
    folder = core.save_folder(long_folder)["id"]
    feed = core.subscribe(base + "/rss", folder, long_feed)["id"]
    core.refresh(feed)
    core.ingest(feed, "long", long_title, base + "/long", "<p>Body.</p>")
    core.ingest(feed, "short", "A short one", base + "/short", "<p>Body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(web_server)

        feed_name = page.locator(".feed-line").filter(has_text=long_feed).locator("span").first
        feed_name.hover()
        expect(feed_name).to_have_attribute("title", long_feed)
        folder_name = page.locator(".folder-line > button:not(.disclosure) > span").first
        folder_name.hover()
        expect(folder_name).to_have_attribute("title", long_folder)

        page.locator(".feed-line").filter(has_text=long_feed).click()
        clipped = page.locator(".row-title").filter(has_text="unusually expansive")
        clipped.hover()
        expect(clipped).to_have_attribute("title", long_title)

        # A name the pane shows in full needs no tooltip repeating it.
        fits = page.locator(".row-title").filter(has_text="A short one")
        fits.hover()
        expect(fits).not_to_have_attribute("title", re.compile(r".*"))
        browser.close()


def test_the_reader_keeps_a_readable_measure_and_rows_keep_real_targets(core, site, web_server):
    """A saved wide list no longer squeezes the reader to a sliver, the row
    and reader controls are large enough to hit, and an unsaved star stays
    out of the way until the row is pointed at."""
    base, _, _ = site
    folder = core.save_folder("Essays")["id"]
    feed = core.subscribe(base + "/rss", folder, "The Sunday Journal")["id"]
    core.refresh(feed)
    body = "<p>" + "A sentence long enough to fill a line of the reading column. " * 40 + "</p>"
    core.ingest(feed, "one", "The first essay", base + "/one", body)
    core.ingest(feed, "two", "The second essay", base + "/two", body)
    # The widths a user had dragged to before, with a large reading size.
    core.save_settings({"sidebar_width": 281, "list_width": 771, "font_size": 22, "width": 75})

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(web_server)
        row = page.locator(".article-row").filter(has_text="The first essay")
        expect(row).to_be_visible()

        reader = page.locator(".reader-pane").bounding_box()
        assert reader["width"] >= round(1400 * 0.36)

        star = row.locator(".star-toggle")
        page.mouse.move(700, 880)
        expect(star).to_have_css("opacity", "0")
        row.hover()
        expect(star).to_have_css("opacity", "1")
        for button in row.locator("button.read-toggle, button.star-toggle").all():
            box = button.bounding_box()
            assert box["width"] >= 24 and box["height"] >= 32

        row.locator(".row-title").click()
        prose = page.locator(".prose")
        expect(prose).to_be_visible()
        # Width in half-em units, a conservative count of characters per line:
        # the old layout gave this reader 264px at 22px, about 24.
        chars = prose.evaluate(
            "e => e.getBoundingClientRect().width / (parseFloat(getComputedStyle(e).fontSize) * 0.5)"
        )
        assert chars >= 36, chars
        for button in page.locator(".reader-meta button").all():
            box = button.bounding_box()
            assert box["width"] >= 24 and box["height"] >= 32

        # A saved star is always shown, not only on hover.
        page.locator(".reader-meta .star-toggle").click()
        page.mouse.move(700, 880)
        expect(row.locator(".star-toggle.starred")).to_have_css("opacity", "1")
        browser.close()


def test_buttons_that_sit_together_share_one_height(core, web_server):
    """The library's action rows used to mix three button heights side by side."""
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(web_server)
        page.get_by_role("button", name="Manage library").click()
        page.get_by_role("button", name="Import & export", exact=True).click()
        heights = {
            round(b.bounding_box()["height"])
            for b in page.locator(".data-export-row .button, .data-export-row .file-button").all()
        }
        assert heights == {32}, heights
        browser.close()


def test_an_article_starts_a_filter_filled_in_from_itself(core, site, web_server):
    """The reader's filter button opens a new rule already aimed at this
    article's title and feed, marking matches read, and saving adds it."""
    base, _, _ = site
    feed = core.subscribe(base + "/rss", "inbox", "The Sunday Journal")["id"]
    core.ingest(feed, "one", "Sponsored: win a (free) trip?", base + "/one", "<p>Body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(web_server)
        page.locator(".article-row .row-title").first.click()
        page.get_by_role("button", name="Add a filter like this article").click()

        form = page.locator(".rule-form")
        expect(form.locator("input[name=name]")).to_have_value("Sponsored: win a (free) trip?")
        # Matched literally, so the title's brackets and question mark are just text.
        expect(form.locator("textarea[name=pattern]")).to_have_value("Sponsored: win a (free) trip?")
        expect(form.get_by_role("switch", name="Regular expression")).not_to_be_checked()
        expect(page.get_by_role("combobox", name="Match field")).to_contain_text("title")
        expect(page.get_by_role("combobox", name="Action")).to_contain_text("read")
        expect(page.get_by_role("combobox", name="Scope")).to_contain_text("The Sunday Journal")

        form.get_by_role("button", name="Add rule", exact=True).click()
        expect(form).to_have_count(0)
        browser.close()

    [rule] = core.rules()
    assert rule["field"] == "title" and rule["action"] == "read"
    assert rule["feed_id"] == feed and not rule["is_regex"]
    assert rule["pattern"] == "Sponsored: win a (free) trip?"


def test_a_feed_is_unsubscribed_from_its_settings(core, site, web_server):
    """The feed's own settings page can unsubscribe, after asking, and the
    stream that showed the feed goes back to all articles."""
    base, _, _ = site
    feed = core.subscribe(base + "/rss", "inbox", "The Sunday Journal")["id"]
    core.ingest(feed, "one", "Finding beauty in the everyday", base + "/one", "<p>Body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(web_server + "/f/" + feed)
        page.get_by_role("button", name="Manage feed: The Sunday Journal").click()
        unsubscribe = page.get_by_role("button", name="Unsubscribe", exact=True)

        page.once("dialog", lambda d: d.dismiss())
        unsubscribe.click()
        expect(unsubscribe).to_be_visible()
        assert [f["id"] for f in core.feeds()] == [feed]

        page.once("dialog", lambda d: d.accept())
        unsubscribe.click()
        expect(page.get_by_text("Unsubscribed · Z to undo")).to_be_visible()
        expect(unsubscribe).to_have_count(0)
        page.get_by_role("button", name="Close library").click()
        expect(page.get_by_role("heading", level=1)).not_to_have_text("The Sunday Journal")
        expect(page).not_to_have_url(re.compile("/f/"))
        browser.close()

    assert core.feeds() == []


def test_middle_clicking_a_title_opens_the_original_in_a_new_tab(core, site, web_server):
    base, _, _ = site
    feed = core.subscribe(base + "/rss", "inbox", "The Sunday Journal")["id"]
    core.ingest(feed, "one", "Finding beauty in the everyday", base + "/one", "<p>Body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(web_server)
        with page.context.expect_page() as opened:
            page.locator(".row-title").click(button="middle")
        assert opened.value.url == base + "/one"
        # The list stays as it was: nothing opened in the reader, nothing read.
        expect(page.locator(".reading-content h1")).to_have_count(0)
        expect(page.locator(".article-row.unread")).to_have_count(1)
        # A feed name, in the list or the feeds pane, opens that feed.
        for name in (page.locator(".article-row .row-feed"), page.locator(".sidebar .feed-line")):
            with page.context.expect_page() as opened:
                name.click(button="middle")
            assert opened.value.url == web_server + "/f/" + feed
            expect(opened.value.locator(".list-heading h1")).to_contain_text("The Sunday Journal")
            opened.value.close()
        expect(page).to_have_url(web_server + "/")
        browser.close()


def test_only_the_feed_list_scrolls_in_the_sidebar(core, site, web_server):
    """The tray and the FEEDS heading stay put while a long feed list scrolls,
    and its scrollbar only shows while it is being scrolled."""
    base, _, _ = site
    for n in range(40):
        core.subscribe(f"{base}/rss?n={n}", "inbox", f"Feed number {n}")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 700})
        page.goto(web_server)
        tree = page.locator(".folder-tree")
        expect(tree.get_by_text("Feed number 0", exact=True)).to_be_visible()
        views = page.get_by_role("navigation", name="Streams").bounding_box()
        heading = page.get_by_role("heading", name="FEEDS").bounding_box()
        hidden = "rgba(0, 0, 0, 0) rgba(0, 0, 0, 0)"
        assert tree.evaluate("e => getComputedStyle(e).scrollbarColor") == hidden

        tree.hover()
        page.mouse.wheel(0, 800)
        expect(tree).to_have_class(re.compile("scrolling"))
        assert tree.evaluate("e => e.scrollTop") > 0
        assert page.get_by_role("navigation", name="Streams").bounding_box() == views
        assert page.get_by_role("heading", name="FEEDS").bounding_box() == heading
        assert page.locator(".sidebar").evaluate("e => e.scrollTop") == 0
        expect(tree).not_to_have_class(re.compile("scrolling"), timeout=3000)
        browser.close()


def test_the_tray_is_always_there_and_the_feeds_pane_only_with_feed_lists(core, site, web_server):
    """The tray at the far left holds the streams, the library, search and a new
    feed; the feeds pane shows only for streams a feed list can narrow."""
    base, _, _ = site
    feed = core.subscribe(base + "/rss", "inbox", "The Sunday Journal")["id"]
    core.ingest(feed, "one", "Finding beauty in the everyday", base + "/one", "<p>Body.</p>")
    core.ingest(feed, "two", "A second story", base + "/two", "<p>Body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(web_server)
        tray = page.get_by_role("navigation", name="Streams")
        expect(tray.locator(".badge")).to_have_text("2")
        assert tray.bounding_box()["x"] == 0
        pane = page.locator(".sidebar")
        expect(pane.get_by_role("heading", name="FEEDS")).to_be_visible()
        # Folders and subscriptions are added from the tray and the library.
        expect(pane.get_by_role("button", name="New folder")).to_have_count(0)
        expect(pane.get_by_role("button", name="Add a subscription")).to_have_count(0)

        for name in ("Saved", "History"):
            tray.get_by_role("button", name=name, exact=True).click()
            expect(tray.get_by_role("button", name=name, exact=True)).to_have_attribute(
                "aria-current", "page"
            )
            expect(pane).to_have_count(0)
        tray.get_by_role("button", name="All articles", exact=True).click()
        expect(pane).to_be_visible()
        pane.locator(".feed-line", has_text="The Sunday Journal").click()
        expect(pane).to_be_visible()

        tray.get_by_role("button", name="Search", exact=True).click()
        expect(page.get_by_role("textbox", name="Search articles")).to_be_focused()
        page.keyboard.press("Escape")
        tray.get_by_role("button", name="Add a subscription", exact=True).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.keyboard.press("Escape")

        # The library screen keeps the tray, and the tray leaves the library.
        tray.get_by_role("button", name="Manage library", exact=True).click()
        library = page.get_by_role("dialog", name="Manage library")
        library_tray = library.get_by_role("navigation", name="Streams")
        expect(library_tray.get_by_role("button", name="Manage library")).to_have_attribute(
            "aria-current", "page"
        )
        assert library_tray.bounding_box()["x"] == 0
        library_tray.get_by_role("button", name="Saved", exact=True).click()
        expect(library).to_have_count(0)
        expect(page).to_have_title("feedelio - Saved for later")
        browser.close()


def test_bookmarked_feeds_narrows_like_all_articles(core, site, web_server):
    """Bookmarked feeds lists only the bookmarked feeds and their folders; a
    feed or folder picked there shows its articles, otherwise all of theirs."""
    base, _, _ = site
    essays = core.save_folder("Essays")["id"]
    news = core.save_folder("News")["id"]
    kept = core.subscribe(base + "/rss?a", essays, "Kept Journal", {"bookmarked": True})["id"]
    other = core.subscribe(base + "/rss?b", essays, "Other Journal")["id"]
    daily = core.subscribe(base + "/rss?c", news, "Daily Wire")["id"]
    for feed, slug in ((kept, "k1"), (kept, "k2"), (other, "o1"), (daily, "d1")):
        core.ingest(feed, slug, "Story " + slug, base + "/" + slug, "<p>Body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(web_server)
        page.get_by_role("navigation", name="Streams").get_by_role(
            "button", name="Bookmarked feeds", exact=True
        ).click()
        pane = page.locator(".sidebar")
        expect(pane.locator(".folder-line")).to_have_text([re.compile("Essays\\s*2")])
        expect(pane.locator(".feed-line")).to_have_text([re.compile("Kept Journal")])
        expect(page.locator(".article-row")).to_have_count(2)

        pane.locator(".feed-line", has_text="Kept Journal").click()
        expect(page).to_have_url(web_server + "/bookmarks/f/" + kept)
        expect(page.locator(".list-heading h1")).to_contain_text("Kept Journal")
        expect(page.locator(".article-row")).to_have_count(2)
        page.reload()
        expect(pane.locator(".feed-line.active")).to_have_text(re.compile("Kept Journal"))

        # The folder shows its bookmarked feeds' articles, not the others'.
        pane.locator(".folder-line > button:not(.disclosure)", has_text="Essays").click()
        expect(page).to_have_url(web_server + "/bookmarks/d/" + essays)
        expect(page.locator(".article-row")).to_have_count(2)
        expect(page.locator(".article-row", has_text="Story o1")).to_have_count(0)
        browser.close()


def test_the_reading_list_has_its_own_view_in_the_tray(core, site, web_server):
    """The book in the tray lists Chrome's Reading List under each page's
    website, with no feeds pane, and the pages stay out of All articles."""
    base, _, _ = site
    feed = core.subscribe(base + "/rss", "inbox", "Real Feed")["id"]
    core.ingest(feed, "f1", "Feed story", base + "/f1", "<p>Body.</p>")
    core.chrome_sync(
        [
            {
                "url": "https://www.example.org/a",
                "title": "Saved page",
                "hasBeenRead": False,
                "lastUpdateTime": 1,
            }
        ]
    )

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(web_server)
        expect(page.locator(".feed-line")).to_have_text([re.compile("Real Feed")])
        expect(page.locator(".article-row")).to_have_text([re.compile("Feed story")])
        reading = page.get_by_role("navigation", name="Streams").get_by_role(
            "button", name="Reading list", exact=True
        )
        expect(reading.locator(".badge")).to_have_text("1")
        reading.click()
        expect(page).to_have_url(web_server + "/reading")
        expect(page).to_have_title("feedelio - Reading list")
        expect(page.locator(".sidebar")).to_have_count(0)
        row = page.locator(".article-row")
        expect(row).to_have_count(1)
        expect(row.locator(".row-feed")).to_have_text("example.org")
        expect(row.locator("button.row-feed")).to_have_count(0)
        # Its website's name opens the website on a middle click.
        with page.context.expect_page() as opened:
            row.locator(".row-feed").click(button="middle")
        assert opened.value.url == "https://www.example.org/"
        opened.value.close()
        row.get_by_role("button", name="Mark read: Saved page").click()
        expect(reading.locator(".badge")).to_have_count(0)
        page.reload()
        expect(page.locator(".article-row")).to_have_count(1)
        browser.close()


def test_podcasts_are_marked_ctrl_z_undoes_and_a_feed_can_arrive_read(core, site, web_server):
    base, _, _ = site
    feed = core.subscribe(base + "/rss", "inbox", "Talk Show")["id"]
    episode = core.ingest(
        feed, "ep", "An episode", base + "/ep", "<p>Notes.</p>", enclosures=[{"href": base + "/ep.mp3"}]
    )
    core.ingest(feed, "post", "A post", base + "/post", "<p>Body.</p>")

    def state(key):
        core.db.commit()  # see the server's writes
        return core.article(episode)[key]

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(web_server)
        row = page.locator(".article-row", has_text="An episode")
        expect(row.locator(".row-marks").get_by_label("Podcast")).to_be_visible()
        expect(page.locator(".article-row", has_text="A post").locator(".row-marks")).to_have_count(0)
        for button, key in (("Mark read: An episode", "read"), ("Star: An episode", "starred")):
            row.get_by_role("button", name=button, exact=True).click()
            deadline = time.monotonic() + 5
            while not state(key) and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            assert state(key)
            page.keyboard.press("Control+z")
            expect(row.get_by_role("button", name=button, exact=True)).to_be_visible()
            assert not state(key)
        page.locator(".feed-line", has_text="Talk Show").click()
        page.get_by_role("button", name="Manage feed: Talk Show").click()
        page.get_by_label("Mark new articles as read").check()
        page.get_by_role("button", name="Save feed settings").click()
        expect(page.get_by_text("Feed settings saved")).to_be_visible()
        browser.close()
    assert core.feeds()[0]["options"]["mark_read"] is True
    assert core.article(core.ingest(feed, "new", "Arrives read", base + "/new"))["read"]


def test_tray_badges_show_four_digits_over_the_line_and_the_chosen_stream_is_a_tab(web_server):
    """A four-digit count shows whole, drawn over the tray's dividing line; the
    chosen stream takes the panes' ground and covers the line like a bookmark."""
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def many(route):
            response = route.fetch()
            data = response.json()
            data["counts"]["unread"] = 4271
            route.fulfill(response=response, body=json.dumps(data))

        page.route("**/api/overview", many)
        page.goto(web_server)
        tray = page.get_by_role("navigation", name="Streams")
        badge = tray.locator(".badge")
        expect(badge).to_have_text("4271")
        edge = tray.bounding_box()
        box = badge.bounding_box()
        assert box["x"] + box["width"] > edge["x"] + edge["width"]
        assert page.evaluate(
            "() => { const b = document.querySelector('.tray .badge'), r = b.getBoundingClientRect();"
            " return document.elementFromPoint(r.right - 3, r.top + r.height / 2).closest('.badge') === b }"
        )
        tab = tray.get_by_role("button", name="All articles").bounding_box()
        assert tab["x"] + tab["width"] == edge["x"] + edge["width"]
        ground = page.evaluate(
            "() => getComputedStyle(document.querySelector('.stream-pane')).backgroundColor"
        )
        active = page.evaluate(
            "() => getComputedStyle(document.querySelector('.tray .active')).backgroundColor"
        )
        assert active == ground
        browser.close()


def test_the_feed_list_sorts_by_any_column_and_shows_when_each_feed_was_added(core, site, web_server):
    """Every column heading sorts the feed list; the title opens the feed's
    settings, so there is no separate edit button."""
    base, _, _ = site
    older = core.subscribe(base + "/rss", "inbox", "Alpha")["id"]
    newer = core.subscribe(base + "/atom", "inbox", "Beta")["id"]
    core.ingest(older, "one", "One", base + "/one", "<p>Body.</p>")
    core.ingest(older, "two", "Two", base + "/two", "<p>Body.</p>")
    with core.db:
        core.db.execute("UPDATE feeds SET created=? WHERE id=?", ("2025-01-02T00:00:00+00:00", older))
        # Alpha was fetched last, but has published nothing for a month: it is
        # stale, and Updated shows (and sorts by) that month, not the fetch.
        core.db.execute(
            "UPDATE feeds SET checked=?, last_article=? WHERE id=?",
            (now(), now(days=-30), older),
        )
        core.db.execute("UPDATE feeds SET checked=? WHERE id=?", (now(minutes=-5), newer))

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(web_server + "/library/feeds")
        titles = page.locator(".feed-table .feed-open")
        head = page.locator(".feed-table .table-head")
        expect(titles).to_have_text(["Alpha", "Beta"])
        expect(page.locator(".feed-table .feed-added")).to_have_text(["Added", "1y", "now"])
        expect(page.locator(".feed-table .icon-button")).to_have_count(0)

        head.get_by_role("button", name="Added").click()  # Newest first.
        expect(titles).to_have_text(["Beta", "Alpha"])
        expect(head.locator("[aria-sort=descending]")).to_have_text("Added")
        head.get_by_role("button", name="Added").click()
        expect(titles).to_have_text(["Alpha", "Beta"])
        head.get_by_role("button", name="Updated").click()  # Most recent first.
        expect(titles).to_have_text(["Beta", "Alpha"])
        expect(page.locator(".feed-table .feed-updated")).to_have_text(["Updated", "5m", "stale 4w"])
        head.get_by_role("button", name="Unread").click()  # Most unread first.
        expect(titles).to_have_text(["Alpha", "Beta"])
        head.get_by_role("button", name="Feed").click()
        expect(titles).to_have_text(["Alpha", "Beta"])
        head.get_by_role("button", name="Feed").click()
        expect(titles).to_have_text(["Beta", "Alpha"])

        page.get_by_role("button", name="Edit Beta", exact=True).click()
        expect(page).to_have_url(web_server + "/library/feeds/" + newer)
        browser.close()


PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_saved_youtube_playlists_have_their_own_view_and_keep_lost_videos(core, web_server):
    core.youtube_sync(
        [
            {
                "id": "PLbread",
                "title": "Bread",
                "videos": [
                    {"id": "video000001", "title": "Sourdough", "channel": "Baker"},
                    {"id": "video000002", "title": "Rye", "channel": "Baker"},
                ],
            },
            {"id": "WL", "title": "Watch later", "videos": []},
        ]
    )
    chapters = [{"start": 0, "title": "Intro"}, {"start": 65, "title": "Dough"}]
    for n, extra in ((1, {}), (2, {"unavailable": now()})):
        core.thumbnail_path(f"video00000{n}").write_bytes(PIXEL)
        meta = {"video_id": f"video00000{n}", "position": n - 1, "checked": now(), "thumbnail": True}
        meta |= {"chapters": chapters, "keywords": ["baking"], **extra}
        with core.db:
            core.db.execute(
                "UPDATE articles SET meta=?,body='<p>About bread.</p>' WHERE guid=?",
                (json.dumps(meta), f"yt:video:video00000{n}"),
            )

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(web_server)
        expect(page.locator(".article-row")).to_have_count(0)  # Not among the subscriptions.
        youtube = page.get_by_role("navigation", name="Streams").get_by_role(
            "button", name="YouTube", exact=True
        )
        expect(youtube.locator(".badge")).to_have_text("2")
        youtube.click()
        expect(page).to_have_url(web_server + "/youtube")
        expect(page.locator(".sidebar h2")).to_have_text("PLAYLISTS")
        expect(page.locator(".playlist-line")).to_have_text([re.compile("Bread"), re.compile("Watch later")])
        page.locator(".playlist-line", has_text="Bread").click()
        expect(page).to_have_url(web_server + "/youtube/f/yt-PLbread")
        expect(page.locator(".list-heading h1")).to_have_text("Bread")
        rows = page.locator(".article-row")
        expect(rows).to_have_count(2)
        thumb = rows.first.locator(".row-thumb")
        expect(thumb).to_be_visible()
        assert page.evaluate("img => img.naturalWidth", thumb.element_handle()) == 1
        expect(page.locator(".article-row", has_text="Rye").locator(".video-status")).to_have_text(
            "Unavailable"
        )

        page.locator(".row-title", has_text="Sourdough").click()
        frame = page.locator(".video-box iframe")
        expect(frame).to_have_attribute("src", re.compile(r"/embed/video000001$"))
        page.get_by_role("button", name=re.compile("Dough")).click()
        expect(frame).to_have_attribute("src", re.compile(r"/embed/video000001\?start=65&autoplay=1$"))
        expect(page.locator(".video-keywords li")).to_have_text(["baking"])

        page.locator(".row-title", has_text="Rye").click()
        expect(page.locator(".video-box iframe")).to_have_count(0)
        gone = page.locator(".video-gone")
        expect(gone.locator("figcaption")).to_contain_text("No longer on YouTube")
        assert page.evaluate("img => img.naturalWidth", gone.locator("img").element_handle()) == 1
        expect(page.locator(".prose")).to_contain_text("About bread.")

        # A reload of the playlist's address lands on it again.
        page.goto(web_server + "/youtube/f/yt-PLbread")
        expect(page.locator(".playlist-line.active")).to_have_text(re.compile("Bread"))
        browser.close()


def test_videos_and_podcasts_have_pages_and_new_feeds_are_offered_their_folders(core, site, web_server):
    """A folder set to show on the Podcasts page leaves All articles for a tray
    page of its own; a feed found to be a podcast is offered only its folders."""
    base, routes, _ = site
    episode = '<item><guid>{0}</guid><title>Episode {0}</title><link>{1}/{0}</link><enclosure url="{1}/{0}.mp3" type="audio/mpeg" length="1"/></item>'
    routes["/podcast"] = (
        200,
        {"Content-Type": "application/rss+xml"},
        f"<rss version='2.0'><channel><title>Talk show</title><link>{base}</link>{episode.format('e1', base)}</channel></rss>",
    )
    shows = core.save_folder("Shows", section="podcasts")["id"]
    core.save_folder("Interviews", parent_id=shows)
    core.save_folder("Essays")
    show = core.subscribe(base + "/podcast?old", shows, "Old show")["id"]
    journal = core.subscribe(base + "/rss", "inbox", "Journal")["id"]
    core.ingest(show, "o1", "An old episode", base + "/o1", "<p>Talk.</p>")
    core.ingest(journal, "j1", "A journal story", base + "/j1", "<p>Body.</p>")

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(web_server)
        expect(page.locator(".article-row")).to_have_text([re.compile("A journal story")])
        pane = page.locator(".sidebar")
        expect(pane.locator(".folder-line", has_text="Shows")).to_have_count(0)

        tray = page.get_by_role("navigation", name="Streams")
        tray.get_by_role("button", name="Podcasts", exact=True).click()
        expect(page).to_have_url(web_server + "/podcasts")
        expect(page.locator(".list-heading h1")).to_have_text("Podcasts")
        # One folder makes the page: its subfolders and feeds are listed directly.
        expect(pane.locator(".folder-line")).to_have_text([re.compile("Interviews")])
        expect(pane.locator(".feed-line")).to_have_text([re.compile("Old show\\s*1")])
        expect(page.locator(".article-row")).to_have_text([re.compile("An old episode")])
        pane.locator(".feed-line", has_text="Old show").click()
        expect(page).to_have_url(web_server + "/podcasts/f/" + show)
        page.reload()
        expect(pane.locator(".feed-line.active")).to_have_text(re.compile("Old show"))
        tray.get_by_role("button", name="Videos", exact=True).click()
        expect(pane).to_contain_text("No videos feeds yet")

        tray.get_by_role("button", name="Add a subscription").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Website or feed URL").fill(base + "/podcast")
        dialog.get_by_role("button", name="Find feeds").click()
        expect(dialog.locator(".discovered")).to_contain_text("Podcast")
        expect(dialog.get_by_role("combobox", name="Folder")).to_contain_text("Shows")
        expect(dialog).to_contain_text("these are the folders on the Podcasts page")
        dialog.get_by_role("combobox", name="Folder").click()
        expect(dialog.get_by_role("listbox", name="Folder").get_by_role("option")).to_have_text(
            ["Shows", "Interviews", "+ New folder…"]
        )
        dialog.get_by_role("listbox", name="Folder").get_by_role("option", name="Interviews").click()
        dialog.get_by_role("button", name="Subscribe", exact=True).click()
        expect(dialog).to_have_count(0)
        added = next(f for f in core.feeds() if f["url"] == base + "/podcast")
        assert added["folder_id"] != shows and added["folder_id"] != "inbox"
        browser.close()
