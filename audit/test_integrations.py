import asyncio
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from playwright.sync_api import expect

from feedelio.core import Core


@pytest.mark.features(6, 7, 11, 22, 25, 43)
def test_restore_full_library_via_browser_and_export_opml(lab):
    with Core(lab.core.root / "source-library") as source:
        folder = source.save_folder("Restored parent")["id"]
        child = source.save_folder("Restored child", folder)["id"]
        feed = source.subscribe(lab.publisher + "/restore", child, "Restored title")["id"]
        article = source.ingest(
            feed,
            "restored",
            "Restored article",
            lab.publisher + "/restored",
            "<p>Restored article body.</p>",
            transcript="Restored transcript.",
        )
        source.change_articles([article], read=True, starred=True, tags=["archive"])
        source.save_settings({"theme": "dark", "font_size": 24})
        backup = source.backup()
    lab.show()
    lab.manager("Data & connections")
    p = lab.page
    p.locator('input[accept=".json"]').set_input_files(
        {"name": "backup.json", "mimeType": "application/json", "buffer": json.dumps(backup).encode()}
    )
    lab.until(lambda: lab.api("articles")["total"] == 1)
    expect(p.locator("html")).to_have_attribute("data-theme", "dark")
    with p.expect_download() as dl:
        p.get_by_role("link", name="Export OPML", exact=True).click()
    xml = Path(dl.value.path()).read_text()
    assert "Restored parent" in xml and "Restored child" in xml and "Restored title" in xml
    assert p.locator('input[accept=".json"]').is_disabled()
    lab.close()
    lab.open_title("Restored article")
    expect(p.locator(".article-tools").get_by_role("button", name="Saved", exact=True)).to_be_visible()
    expect(p.locator(".transcript")).to_contain_text("Restored transcript")
    assert p.locator(".prose").evaluate("(e)=>getComputedStyle(e).fontSize") == "24px"


@pytest.mark.features(11, 20, 42, 51)
def test_mcp_actions_are_visible_in_real_browser(lab):
    lab.seed(
        articles=[
            {"title": "An agent-readable article", "body": "<p>Heliospheric observations for the agent.</p>"}
        ]
    )
    lab.show()
    article = lab.api("articles")["items"][0]

    async def act():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "feedelio.mcp_server"], env=lab.env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool("search_articles", {"query": "heliospheric"})
                result = response.structuredContent or json.loads(response.content[0].text)
                assert result["total"] == 1
                result = await session.call_tool(
                    "set_article_state", {"article_id": article["id"], "starred": True}
                )
                assert not result.isError

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(lambda: asyncio.run(act())).result(timeout=20)
    lab.page.reload()
    lab.open_title(article["title"])
    expect(lab.page.locator(".article-tools").get_by_role("button", name="Saved", exact=True)).to_be_visible()


@pytest.mark.features(47, 52, 9, 26)
def test_real_chromium_extension_reading_list_round_trip(lab, playwright, tmp_path):
    # Only substitute the default server address to prevent extension installation
    # from contacting a user's existing localhost:8000 library. No Chrome API mocks.
    original = Path(__file__).resolve().parents[1] / "extension"
    extension = tmp_path / "isolated-extension"
    shutil.copytree(original, extension)
    for filename in ("background.js", "options.js", "manifest.json"):
        path = extension / filename
        path.write_text(
            path.read_text()
            .replace("http://localhost:8000", lab.base)
            .replace("http://127.0.0.1:8000", lab.base)
        )
    context = playwright.chromium.launch_persistent_context(
        str(tmp_path / "chrome-profile"),
        channel="chromium",
        headless=True,
        args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
    )
    context.tracing.start(screenshots=True, snapshots=True)
    try:
        worker = (
            context.service_workers[0]
            if context.service_workers
            else context.wait_for_event("serviceworker", timeout=15000)
        )
        extension_id = worker.url.split("/")[2]
        page = context.new_page()
        page.set_default_timeout(8000)
        page.goto(f"chrome-extension://{extension_id}/options.html")
        page.locator("#server").fill(lab.base)
        page.get_by_role("button", name="Save & sync", exact=True).click()
        expect(page.locator("#status")).to_contain_text("Saved. Last sync:", timeout=15000)
        assert awaitable_reading_list(worker)
        url = lab.publisher + "/reading-list-item"
        worker.evaluate(
            'async url=>{await chrome.readingList.addEntry({url,title:"Real Chrome saved item",hasBeenRead:false})}',
            url,
        )
        lab.until(lambda: any(a["title"] == "Real Chrome saved item" for a in lab.api("articles")["items"]))
        lab.show()
        lab.open_title("Real Chrome saved item")
        # Real browser page opening sets read=true; request sync through the extension's own UI.
        lab.until(lambda: lab.api("articles")["items"][0]["read"] == 1)
        page.get_by_role("button", name="Save & sync", exact=True).click()
        expect(page.locator("#status")).to_contain_text("Saved. Last sync:", timeout=15000)
        lab.until(
            lambda: worker.evaluate("async url=>(await chrome.readingList.query({url}))[0].hasBeenRead", url)
        )
        worker.evaluate("async url=>{await chrome.readingList.updateEntry({url,hasBeenRead:false})}", url)
        lab.until(lambda: lab.api("articles")["items"][0]["read"] == 0)
        lab.page.reload()
        expect(lab.page.locator(".article-row.unread")).to_have_count(1)
        # Real subscribe deep link opened by the companion's toolbar handler.
        lab.page.goto(
            lab.base
            + "/?subscribe="
            + __import__("urllib.parse", fromlist=["quote"]).quote(lab.publisher + "/homepage", safe="")
        )
        expect(lab.page.get_by_role("textbox", name="Website or feed URL")).to_have_value(
            lab.publisher + "/homepage"
        )
        page.screenshot(path=str(Path("test-results/adversarial") / "extension-options.png"))
    finally:
        context.tracing.stop(path=str(Path("test-results/adversarial") / "extension-trace.zip"))
        context.close()


def awaitable_reading_list(worker):
    return worker.evaluate('()=>typeof chrome.readingList?.query === "function"')
