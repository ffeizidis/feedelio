import pytest
from playwright.sync_api import expect


@pytest.mark.features(7, 8, 13, 22)
def test_right_pane_streams_scroll_resize_and_persist_display(lab):
    folder = lab.core.save_folder("Long reads")["id"]
    lab.seed(
        "Journal",
        [
            {"title": f"Story {i}", "body": "<p>" + "A readable preview and long article. " * 700 + "</p>"}
            for i in range(35)
        ],
        folder,
    )
    lab.show()
    p = lab.page
    p.locator(".feed-line").filter(has_text="Journal").click()
    expect(p.locator(".reader-pane .article-row")).to_have_count(35)
    expect(p.locator(".sidebar .article-row")).to_have_count(0)
    listing = p.locator(".article-list")
    assert listing.evaluate("e => e.scrollHeight > e.clientHeight")
    assert listing.evaluate("e => getComputedStyle(e).overflowY") == "scroll"
    assert listing.evaluate("e => e.offsetWidth - e.clientWidth") >= 12
    listing.hover()
    p.mouse.wheel(0, 600)
    lab.until(lambda: listing.evaluate("e => e.scrollTop > 100"))
    divider = p.get_by_role("separator", name="Resize panes")
    box = divider.bounding_box()
    p.mouse.move(box["x"] + 4, box["y"] + 200)
    p.mouse.down()
    p.mouse.move(box["x"] + 124, box["y"] + 200)
    p.mouse.up()
    lab.until(lambda: lab.api("overview")["settings"]["sidebar_width"] > 400)
    assert p.locator(".sidebar").bounding_box()["width"] > 400
    divider.focus()
    p.keyboard.press("ArrowLeft")
    lab.until(lambda: lab.api("overview")["settings"]["sidebar_width"] < 440)
    p.get_by_role("combobox", name="Article list display").select_option("title")
    expect(p.locator(".article-row p")).to_have_count(0)
    p.reload()
    expect(p.get_by_role("combobox", name="Article list display")).to_have_value("title")
    assert p.locator(".sidebar").bounding_box()["width"] > 400
    p.get_by_role("combobox", name="Article list display").select_option("preview")
    expect(p.locator(".article-row p")).to_have_count(35)
    p.locator(".article-row").first.click()
    expect(listing).not_to_be_visible()
    reading = p.locator(".reading-scroll")
    expect(reading).to_be_visible()
    assert reading.evaluate("e => e.scrollHeight > e.clientHeight")
    assert reading.evaluate("e => getComputedStyle(e).overflowY") == "scroll"
    assert reading.evaluate("e => e.offsetWidth - e.clientWidth") >= 12
    reading.hover()
    p.mouse.wheel(0, 800)
    lab.until(lambda: reading.evaluate("e => e.scrollTop > 100"))
    assert p.locator(".reader-pane").bounding_box()["height"] == 1000
    p.get_by_role("button", name="Back to list", exact=True).click()
    expect(listing).to_be_visible()
    p.locator(".folder-line > button:not(.disclosure)").filter(has_text="Long reads").click()
    expect(p.locator(".reader-pane .list-heading h1")).to_have_text("Long reads")
    expect(p.locator(".reader-pane .article-row")).to_have_count(35)


@pytest.mark.features(5, 46)
def test_archive_progress_pause_resume_and_failure_visible(lab):
    feed = lab.seed("Archive source", [{"title": "Recent story"}])
    job = lab.core.enqueue("backfill", {"feed_id": feed})["id"]
    lab.show()
    p = lab.page
    progress = p.get_by_label("Archive progress")
    expect(progress).to_contain_text("Archive waiting")
    p.get_by_role("button", name="Pause archive", exact=True).click()
    expect(progress).to_contain_text("Archive paused")
    p.get_by_role("button", name="Resume archive", exact=True).click()
    expect(progress).to_contain_text("Archive waiting")
    with lab.core.db:
        lab.core.db.execute(
            "UPDATE jobs SET status='failed',error='Publisher returned 403; archive access unavailable' WHERE id=?",
            (job,),
        )
    p.reload()
    expect(progress).to_contain_text("archive access unavailable")
    expect(p.get_by_role("button", name="Resume archive", exact=True)).to_be_visible()
