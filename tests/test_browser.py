"""Desktop acceptance test against the built React app and the real API."""

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
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(web_server)
        expect(page.locator(".reader-pane .article-list")).to_be_visible()
        expect(page.locator(".article-row")).to_have_count(2)
        page.keyboard.press("j")
        expect(page.locator(".reading-content h1")).to_be_visible()
        page.keyboard.press("s")
        expect(page.locator(".article-tools").get_by_role("button", name="Saved", exact=True)).to_be_visible()
        page.keyboard.press("/")
        expect(page.get_by_role("textbox", name="Search articles")).to_be_focused()
        page.get_by_role("textbox", name="Search articles").fill("independent")
        expect(page.locator(".article-row")).to_have_count(1)
        page.locator(".article-row").click()
        expect(page.locator(".reading-content h1")).to_have_text("A slower and more thoughtful internet")
        page.get_by_role("button", name="Clear search", exact=True).click()
        page.get_by_role("button", name="Reading appearance").click()
        page.get_by_role("button", name="Dark", exact=True).click()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        page.get_by_role("button", name="Light", exact=True).click()
        page.get_by_role("button", name="Close dialog").click()
        output = Path("test-results")
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / "desktop.png"), full_page=True)
        page.get_by_role("button", name="Manage library").click()
        page.get_by_role("button", name="Folders", exact=True).click()
        page.get_by_role("textbox", name="New folder").fill("Engineering")
        page.get_by_role("button", name="Create folder").click()
        expect(page.get_by_role("textbox", name="Folder name").filter(visible=True)).to_have_count(2)
        page.get_by_role("button", name="Rules", exact=True).click()
        page.get_by_role("button", name="+ Skip YouTube Shorts", exact=True).click()
        expect(page.locator(".rule-row")).to_have_count(1)
        page.get_by_role("button", name="Data & connections", exact=True).click()
        expect(page.get_by_role("link", name="Full JSON backup")).to_be_visible()
        page.get_by_role("button", name="Close dialog").click()
        page.keyboard.press("?")
        expect(page.get_by_role("heading", name="Make yourself at home")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("dialog")).to_have_count(0)
        # Desktop-only shell remains two panes at 200% zoom, with horizontal scrolling.
        page.locator(".article-row").first.click()
        page.evaluate("document.documentElement.style.zoom='2'")
        expect(page.locator(".prose")).to_be_visible()
        assert (
            page.locator(".shell").evaluate(
                '(el)=>getComputedStyle(el).gridTemplateColumns.split(" ").length'
            )
            == 3  # Two panes and their draggable separator.
        )
        assert not errors, errors
        browser.close()
