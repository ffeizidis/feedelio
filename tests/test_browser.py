"""Desktop acceptance test against the built React app and the real API."""

import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from feedelio.api import app


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
        # Desktop-only shell retains three panes at 200% zoom, with horizontal scrolling.
        page.locator(".article-row .row-title").first.click()
        page.evaluate("document.documentElement.style.zoom='2'")
        expect(page.locator(".prose")).to_be_visible()
        assert (
            page.locator(".shell").evaluate(
                '(el)=>getComputedStyle(el).gridTemplateColumns.split(" ").length'
            )
            == 3
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
