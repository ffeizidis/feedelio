import re

import pytest
from playwright.sync_api import expect


@pytest.mark.features(9, 11, 12, 20, 26, 27, 32)
@pytest.mark.parametrize("view", ["search", "saved", "history", "tag"])
def test_mark_filtered_stream_leaves_unrelated_articles_unread(lab, view):
    lab.seed(articles=[{"title": "Target story"}, {"title": "Unrelated story"}])
    target = next(a["id"] for a in lab.core.articles()["items"] if a["title"] == "Target story")
    lab.core.change_articles([target], read=True, opened=True, starred=True, tags=["target"])
    lab.core.change_articles([target], read=False)
    lab.show()
    p = lab.page
    if view == "search":
        p.get_by_role("textbox", name="Search articles").fill("Target")
    elif view == "saved":
        p.locator(".views").get_by_role("button", name=re.compile("Saved")).click()
    elif view == "history":
        p.locator(".views").get_by_role("button", name="History", exact=True).click()
    else:
        lab.open_title("Target story")
        p.keyboard.press("m")
        expect(p.locator(".article-tools").get_by_role("button", name="Unread", exact=True)).to_be_visible()
        p.locator(".article-tags").get_by_role("button", name="target", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(1)
    p.get_by_role("button", name="Mark this stream as read", exact=True).click()
    lab.until(lambda: lab.api("articles/" + target)["read"])
    articles = lab.api("articles?deduplicate=false")["items"]
    assert not next(a for a in articles if a["title"] == "Unrelated story")["read"]
    p.keyboard.press("u")
    lab.until(lambda: not lab.api("articles/" + target)["read"])
    assert lab.api("overview")["counts"]["unread"] == 2


@pytest.mark.features(8, 9, 13)
def test_unread_pagination_recovers_when_last_page_disappears(lab):
    lab.seed(articles=[{"title": f"Article {i}"} for i in range(101)])
    lab.show()
    p = lab.page
    p.get_by_role("button", name="Unread only", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(100)
    p.locator(".pagination").get_by_role("button", name=re.compile("Next")).click()
    expect(p.locator(".article-row")).to_have_count(1)
    p.locator(".article-row").click()
    expect(p.locator(".reading-content h1")).to_be_visible()
    expect(p.locator(".article-row")).to_have_count(100)
    assert lab.api("overview")["counts"]["unread"] == 100
