"""Three-pane design acceptance against real FastAPI, SQLite and Chromium."""

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect


def seed(lab):
    folder = lab.core.save_folder("Essays")["id"]
    lab.seed(
        "Sasha’s Newsletter",
        [
            {
                "title": f"Notes on making things slowly — chapter {i}",
                "body": "<p>Attention is a form of generosity. Read slowly and notice the world.</p>" * 80,
            }
            for i in range(40)
        ],
        folder,
    )
    lab.seed(
        "Craft & Method",
        [
            {
                "title": "The quiet everyday art of reading",
                "body": "<p>A thoughtful story about attention.</p>",
            }
        ],
        path="/craft",
    )
    lab.show()


@pytest.mark.features(8, 13, 22)
def test_feed_list_omits_repeated_source_and_sidebar_is_larger(lab):
    seed(lab)
    p = lab.page
    expect(p.locator(".row-feed")).to_have_count(41)
    p.locator(".feed-line").filter(has_text="Sasha’s Newsletter").click()
    expect(p.locator(".article-row")).to_have_count(40)
    expect(p.locator(".row-feed")).to_have_count(0)
    for key in ("1", "2", "3"):
        p.keyboard.press(key)
        row = p.locator(".article-row").first
        assert row.evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == 3
        expect(row.locator(".row-title")).to_be_visible()
        expect(p.locator(".row-feed")).to_have_count(0)
        if key == "2":
            expect(row.locator(".row-snippet")).to_be_visible()
            assert (
                row.locator(".row-snippet").bounding_box()["x"]
                == row.locator(".row-title").bounding_box()["x"]
            )
    p.keyboard.press("/")
    p.get_by_role("textbox", name="Search articles").fill("chapter")
    expect(p.locator(".article-row")).to_have_count(40)
    expect(p.locator(".row-feed")).to_have_count(0)
    assert p.locator(".row-snippet").first.evaluate("e=>getComputedStyle(e).gridRowStart") == "2"
    p.keyboard.press("Escape")
    p.keyboard.press("1")
    p.locator(".folder-line > button:not(.disclosure)").filter(has_text="Essays").click()
    expect(p.locator(".row-feed")).to_have_count(40)
    p.keyboard.press("g")
    p.keyboard.press("a")
    expect(p.locator(".row-feed")).to_have_count(41)
    for selector, size in [
        (".views button", 18),
        (".feed-line", 17),
        (".folder-line > button:not(.disclosure)", 16),
        (".search-trigger", 16),
    ]:
        assert p.locator(selector).first.evaluate("e=>parseFloat(getComputedStyle(e).fontSize)") == size
    icon_url = p.locator('link[rel="icon"]').get_attribute("href")
    assert "script-f" in icon_url
    icon = p.request.get(lab.base + icon_url)
    assert icon.ok and "Italianno" in icon.text() and "<text" not in icon.text()
    assert p.evaluate(
        """src => new Promise(resolve => {
        const image = new Image(); image.onload = () => resolve(image.naturalWidth > 0);
        image.onerror = () => resolve(false); image.src = src;
    })""",
        icon_url,
    )


@pytest.mark.features(7, 8, 9, 11, 13, 22)
@pytest.mark.parametrize("width", [1240, 1440, 1600, 1900, 2560])
def test_three_panes_geometry_and_independent_targets(lab, width):
    p = lab.page
    p.set_viewport_size({"width": width, "height": 900})
    seed(lab)
    panes = [p.locator(s).bounding_box() for s in (".sidebar", ".stream-pane", ".reader-pane")]
    assert all(b["y"] == 0 and b["height"] == 900 for b in panes)
    assert panes[0]["width"] == 252 and panes[1]["width"] == 590
    assert panes[1]["x"] == panes[0]["width"]
    assert panes[2]["x"] == panes[0]["width"] + panes[1]["width"]
    assert panes[2]["x"] + panes[2]["width"] == width
    row = p.locator(".article-row").first
    assert row.bounding_box()["height"] == 38
    row.locator(".star-toggle").click()
    expect(row.locator(".star-toggle")).to_have_attribute("aria-pressed", "true")
    expect(p.locator(".reading-content")).to_have_count(0)
    row.locator(".read-toggle").click()
    expect(row).to_have_class(re.compile("read$"))
    expect(p.locator(".reading-content")).to_have_count(0)
    p.locator(".article-row").filter(has_text="chapter 0").locator(".row-title").click()
    expect(p.locator(".reading-content")).to_be_visible()
    expect(p.locator(".article-list")).to_be_visible()
    assert p.locator(".reading-scroll").evaluate("e=>e.scrollHeight>e.clientHeight")
    p.locator(".reading-scroll").hover()
    p.mouse.wheel(0, 500)
    lab.until(lambda: p.locator(".reading-scroll").evaluate("e=>e.scrollTop") > 100)
    p.locator(".article-list").hover()
    p.mouse.wheel(0, 400)
    lab.until(lambda: p.locator(".article-list").evaluate("e=>e.scrollTop") > 100)
    for name, setting in [("Resize feeds pane", "sidebar_width"), ("Resize article list", "list_width")]:
        handle = p.get_by_role("separator", name=name)
        box = handle.bounding_box()
        before = float(handle.get_attribute("aria-valuenow"))
        p.mouse.move(box["x"] + 4, 220)
        p.mouse.down()
        p.mouse.move(box["x"] + 34, 220)
        p.mouse.up()
        lab.until(lambda: lab.api("overview")["settings"][setting] > before)
    p.keyboard.press("3")
    expect(p.locator(".article-list")).to_have_class("article-list compact")
    p.reload()
    expect(p.locator(".article-list")).to_have_class("article-list compact")
    assert p.locator(".article-row").first.bounding_box()["height"] == 28
    p.keyboard.press("2")
    expect(p.locator(".row-snippet").first).to_be_visible()
    p.keyboard.press("1")
    expect(p.locator(".row-snippet")).to_have_count(0)


@pytest.mark.features(7, 10, 13, 20, 22, 24, 25)
def test_settings_search_folders_and_design_screens(lab):
    seed(lab)
    p = lab.page
    output = Path("test-results/redesign")
    output.mkdir(parents=True, exist_ok=True)
    p.set_viewport_size({"width": 1240, "height": 900})
    p.locator(".article-row").filter(has_text="chapter 0").locator(".row-title").click()
    p.screenshot(path=str(output / "reading-dark.png"))
    p.keyboard.press("/")
    p.get_by_role("textbox", name="Search articles").fill("attention")
    expect(p.locator(".article-row")).to_have_count(41)
    expect(p.locator("mark").first).to_be_visible()
    p.get_by_role("button", name="Titles only", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(0)
    p.get_by_role("textbox", name="Search articles").fill("quiet")
    expect(p.locator(".article-row")).to_have_count(1)
    p.screenshot(path=str(output / "search.png"))
    p.keyboard.press("Escape")
    expect(p.get_by_role("textbox", name="Search articles")).to_have_count(0)
    p.keyboard.press("?")
    expect(p.get_by_role("heading", name="Keyboard", exact=True)).to_be_visible()
    p.screenshot(path=str(output / "keyboard.png"))
    p.keyboard.press("?")
    expect(p.locator("dialog")).to_have_count(0)
    p.keyboard.press(",")
    expect(p.locator(".manager-title h1")).to_have_text("Feeds")
    p.screenshot(path=str(output / "feeds.png"))
    p.get_by_role("button", name="Folders", exact=True).click()
    p.screenshot(path=str(output / "folders.png"))
    p.get_by_role("button", name="+ New folder", exact=True).click()
    p.get_by_role("textbox", name="Folder name").fill("New notes")
    p.screenshot(path=str(output / "new-folder.png"))
    p.get_by_role("button", name="Create folder", exact=True).click()
    expect(p.locator(".folder-group-heading").filter(has_text="New notes")).to_be_visible()
    source = p.locator(".folder-child").filter(has_text="Craft & Method")
    dest = p.locator(".folder-group-heading").filter(has_text="New notes")
    source.drag_to(dest)
    lab.until(
        lambda: (
            next(f for f in lab.api("overview")["feeds"] if f["title"] == "Craft & Method")["folder_id"]
            != "inbox"
        )
    )
    p.get_by_role("button", name="Appearance", exact=True).click()
    p.screenshot(path=str(output / "appearance.png"))
    p.get_by_role("button", name="Light", exact=True).click()
    expect(p.locator("html")).to_have_attribute("data-theme", "light")
    p.get_by_role("button", name="violet accent", exact=True).click()
    expect(p.locator("html")).to_have_attribute("data-accent", "violet")
    p.get_by_role("switch", name="Feed colours", exact=True).click()
    expect(p.locator("html")).to_have_attribute("data-feed-colours", "false")
    p.get_by_role("button", name="Newsreader", exact=True).click()
    p.get_by_role("button", name="Increase text size", exact=True).click()
    p.get_by_role("button", name="Close library", exact=True).click()
    expect(p.locator(".reading-content")).to_have_class("reading-content font-newsreader")
    assert p.locator(".prose").evaluate("e=>getComputedStyle(e).fontSize") == "18px"
    p.screenshot(path=str(output / "reading-light.png"))
    assert p.locator("body").evaluate("e=>getComputedStyle(e).backgroundColor") == "rgb(237, 239, 233)"
    p.keyboard.press(",")
    p.get_by_role("button", name="Import & export", exact=True).click()
    expect(p.get_by_role("link", name="Export JSON", exact=True)).to_be_visible()
    p.screenshot(path=str(output / "import-export.png"))
    p.get_by_role("button", name="Close library", exact=True).click()
    p.get_by_role("button", name="Add a subscription", exact=True).click()
    p.screenshot(path=str(output / "add-feed.png"))
    p.keyboard.press("Escape")
    p.evaluate("document.documentElement.style.zoom='2'")
    expect(p.locator(".prose")).to_be_visible()
    assert p.locator(".shell").evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == 3


@pytest.mark.features(10, 22)
def test_feed_hue_contrast_and_local_fonts(lab):
    seed(lab)
    p = lab.page
    p.evaluate("document.fonts.ready")
    assert p.evaluate("document.fonts.check('62px Italianno')")
    for theme in ("dark", "light"):
        ratios = p.evaluate(
            """theme => {
          document.documentElement.dataset.theme = theme;
          const canvas = document.createElement('canvas'), c = canvas.getContext('2d');
          function luminance(color) { c.fillStyle=color; c.fillRect(0,0,1,1); const rgb=[...c.getImageData(0,0,1,1).data].slice(0,3).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}); return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722; }
          const ground=luminance(getComputedStyle(document.body).backgroundColor);
          return Array.from({length:14},(_,i)=>{const el=document.createElement('span');el.className='row-feed feed-hue-'+i;document.body.append(el);const ink=luminance(getComputedStyle(el).color);el.remove();return (Math.max(ground,ink)+.05)/(Math.min(ground,ink)+.05)});
        }""",
            theme,
        )
        assert min(ratios) >= 4.5, (theme, ratios)
    fonts = p.evaluate(
        "performance.getEntriesByType('resource').filter(x=>/woff/.test(x.name)).map(x=>x.name)"
    )
    assert fonts and all(url.startswith(lab.base) for url in fonts)


@pytest.mark.features(9, 11, 12, 13, 22, 26)
def test_confirmation_shortcuts_and_original_title_target(lab):
    lab.seed(articles=[{"title": f"Story {i}"} for i in range(51)])
    lab.show()
    p = lab.page
    dialogs = []

    def dismiss(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    p.on("dialog", dismiss)
    p.get_by_role("button", name="Mark this stream as read", exact=True).click()
    assert len(dialogs) == 1 and "51" in dialogs[0]
    assert lab.api("overview")["counts"]["unread"] == 51
    p.remove_listener("dialog", dismiss)
    p.once("dialog", lambda dialog: dialog.accept())
    p.keyboard.press("Shift+A")
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 0)
    p.keyboard.press("z")
    lab.until(lambda: lab.api("overview")["counts"]["unread"] == 51)
    p.keyboard.press("u")
    expect(p.get_by_role("switch", name="Unread only")).to_have_attribute("aria-checked", "true")
    p.keyboard.press("u")
    expect(p.get_by_role("switch", name="Unread only")).to_have_attribute("aria-checked", "false")
    before = lab.api("overview")["settings"]["list_width"]
    p.keyboard.press("[")
    lab.until(lambda: lab.api("overview")["settings"]["list_width"] == before - 20)
    p.keyboard.press("]")
    lab.until(lambda: lab.api("overview")["settings"]["list_width"] == before)
    lab.manager("Appearance")
    p.get_by_role("button", name="Original page", exact=True).click()
    lab.until(lambda: lab.api("overview")["settings"]["open_titles_in"] == "original")
    lab.close()
    with p.expect_popup() as popup:
        p.locator(".row-title").first.click()
    popup.value.close()
    expect(p.locator(".reading-content")).to_have_count(0)
    assert lab.api("overview")["counts"]["unread"] == 51
    # Enter always explicitly opens in the reader, independent of title-click preference.
    p.keyboard.press("Enter")
    expect(p.locator(".reading-content")).to_be_visible()
    p.keyboard.press("s")
    lab.until(lambda: lab.api("overview")["counts"]["starred"] == 1)
    p.keyboard.press("g")
    p.keyboard.press("s")
    expect(p.locator(".article-row")).to_have_count(1)
    p.keyboard.press("g")
    p.keyboard.press("a")
    expect(p.locator(".article-row")).to_have_count(51)


@pytest.mark.features(6, 11, 24, 26, 43)
def test_redesigned_import_exports_and_remove_all_are_real(lab):
    lab.seed(articles=[{"title": "Keep this essay"}])
    item = lab.core.articles()["items"][0]
    lab.core.change_articles([item["id"]], starred=True)
    lab.show()
    p = lab.page
    lab.manager("Import & export")
    with p.expect_download() as download:
        p.get_by_role("link", name="Export JSON", exact=True).click()
    saved = json.loads(Path(download.value.path()).read_text())
    assert saved["format"] == "feedelio-starred"
    assert [a["title"] for a in saved["articles"]] == ["Keep this essay"]
    xml = f'<opml><body><outline text="Imported folder"><outline text="New source" xmlUrl="{lab.publisher}/new"/></outline></body></opml>'
    transfer = p.evaluate_handle(
        """xml => {const data=new DataTransfer(); data.items.add(new File([xml], 'library.opml', {type:'application/xml'})); return data;}""",
        xml,
    )
    p.locator(".opml-drop").dispatch_event("drop", {"dataTransfer": transfer})
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 2)
    expect(p.get_by_role("status").filter(has_text="Last import")).to_contain_text("1 subscriptions imported")
    p.once("dialog", lambda d: d.dismiss())
    p.get_by_role("button", name="Remove all", exact=True).click()
    assert len(lab.api("overview")["feeds"]) == 2
    p.once("dialog", lambda d: d.accept())
    p.get_by_role("button", name="Remove all", exact=True).click()
    lab.until(lambda: not lab.api("overview")["feeds"])
    lab.close()
    p.keyboard.press("z")
    lab.until(lambda: len(lab.api("overview")["feeds"]) == 2)
    assert lab.api("overview")["counts"]["starred"] == 1
