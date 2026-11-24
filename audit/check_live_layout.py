"""Explicit live-browser smoke check (opens one article and restores UI preferences).

Run manually after deployment: .venv/bin/python audit/check_live_layout.py
"""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    artifacts = Path("test-results/live-redesign")
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(ignore_default_args=["--hide-scrollbars"])
        context = browser.new_context(viewport={"width": 1900, "height": 1018})
        page = context.new_page()
        errors, results = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://localhost:8000")
        expect(page.locator(".brand")).to_be_visible()
        expect(page.get_by_label("Access token")).to_have_count(0)
        overview = page.request.get("http://localhost:8000/api/overview")
        assert overview.ok
        settings = overview.json()["settings"]

        def geometry(label):
            page.wait_for_function(
                "() => Math.abs(document.querySelector('.reader-pane').getBoundingClientRect().right - innerWidth) < 1"
            )
            sidebar = page.locator(".sidebar").bounding_box()
            divider = page.get_by_role("separator", name="Resize feeds pane").bounding_box()
            listing = page.locator(".stream-pane").bounding_box()
            reader = page.locator(".reader-pane").bounding_box()
            assert sidebar["y"] == divider["y"] == reader["y"] == 0
            assert sidebar["height"] == divider["height"] == reader["height"] == 1018
            # Adjacent pane edges meet; the sidebar starts after the 64px tray, not at 0.
            assert abs(listing["x"] - sidebar["x"] - sidebar["width"]) < 1
            assert abs(reader["x"] - listing["x"] - listing["width"]) < 1
            assert abs(reader["x"] + reader["width"] - page.viewport_size["width"]) < 1
            results.append({"view": label, "viewport": page.viewport_size, "reader": reader})

        try:
            # Make already-read articles visible so the smoke check needn't consume an unread item.
            response = page.request.post(
                "http://localhost:8000/api/actions/save_settings",
                data={"payload": {"values": {"hide_read": False}}},
            )
            assert response.ok
            page.reload()
            page.locator(".feed-line").first.click()
            expect(page.locator(".article-row").first).to_be_visible()
            expect(page.locator(".row-feed")).to_have_count(0)
            assert page.locator(".feed-line").first.evaluate("e=>getComputedStyle(e).fontSize") == "19px"
            icon = page.request.get("http://localhost:8000/favicon.svg?v=script-f")
            assert icon.ok and "Italianno" in icon.text()
            for width in (1240, 1440, 1599, 1600, 1900, 2560):
                page.set_viewport_size({"width": width, "height": 1018})
                geometry("source list")
            page.set_viewport_size({"width": 1900, "height": 1018})
            separator = page.get_by_role("separator", name="Resize feeds pane")
            box = separator.bounding_box()
            page.mouse.move(box["x"] + 4, 300)
            page.mouse.down()
            page.mouse.move(page.locator(".sidebar").bounding_box()["x"] + 384, 300)
            page.mouse.up()
            expect(separator).to_have_attribute("aria-valuenow", "384")
            geometry("resized source list")
            list_handle = page.get_by_role("separator", name="Resize article list")
            list_handle.focus()
            page.keyboard.press("ArrowLeft")
            list_handle.blur()
            geometry("resized article list")
            page.keyboard.press("1")
            expect(page.locator(".article-row p")).to_have_count(0)
            page.keyboard.press("2")
            expect(page.locator(".article-row p").first).to_be_visible()
            page.screenshot(path=str(artifacts / "source-list.png"))
            page.locator(".article-row.read .row-title").first.click()
            reading = page.locator(".reading-scroll")
            expect(reading).to_be_visible()
            geometry("open article")
            assert reading.evaluate("e => e.scrollHeight > e.clientHeight")
            assert reading.evaluate("e => e.offsetWidth - e.clientWidth") >= 12
            reading.hover()
            page.mouse.wheel(0, 700)
            # Wheel scrolling is asynchronous.
            page.wait_for_timeout(500)
            assert reading.evaluate("e => e.scrollTop") > 100
            page.screenshot(path=str(artifacts / "article.png"))
            expect(page.locator(".article-list")).to_be_visible()
            page.keyboard.press("Escape")
            page.locator(".folder-line > button:not(.disclosure)").first.click()
            expect(page.locator(".article-row").first).to_be_visible()
            geometry("folder list")
            expect(page.locator(".row-feed").first).to_be_visible()
            page.reload()
            expect(page.locator(".brand")).to_be_visible()
            expect(page.get_by_label("Access token")).to_have_count(0)
            assert not context.cookies("http://localhost:8000")
            assert not errors, errors
        finally:
            response = page.request.post(
                "http://localhost:8000/api/actions/save_settings",
                data={
                    "payload": {
                        "values": {
                            key: settings[key]
                            for key in ("sidebar_width", "list_width", "list_display", "hide_read")
                        }
                    }
                },
            )
            assert response.ok
            context.close()
            browser.close()
        report = {"passed": True, "no_login": True, "console_errors": errors, "geometry": results}
        (artifacts / "results.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
