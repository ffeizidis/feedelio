"""Additional browser checks around the repaired navigation boundaries."""

import pytest
from playwright.sync_api import expect


@pytest.mark.features(8, 9, 13)
def test_navigation_survives_an_empty_unread_list(lab):
    lab.seed(
        articles=[
            {"title": "First", "published": "2026-09-17T12:00:00+00:00"},
            {"title": "Second", "published": "2026-09-16T12:00:00+00:00"},
        ]
    )
    lab.show()
    p = lab.page
    p.get_by_role("switch", name="Unread only", exact=True).click()
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text("First")
    expect(p.locator(".article-row")).to_have_count(1)
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text("Second")
    expect(p.locator(".article-row")).to_have_count(0)
    # Exhausting the list must not disable navigation through the reading trail.
    p.keyboard.press("k")
    expect(p.locator(".reading-content h1")).to_have_text("First")
    p.keyboard.press("k")
    expect(p.locator(".reading-content h1")).to_have_text("First")
    p.keyboard.press("Escape")
    p.get_by_role("switch", name="Unread only", exact=True).click()
    expect(p.locator(".article-row")).to_have_count(2)
    p.keyboard.press("j")
    # Returning to the list and changing its filter starts at the first item.
    expect(p.locator(".reading-content h1")).to_have_text("First")
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text("Second")


@pytest.mark.features(7, 8, 13)
def test_navigation_follows_folder_scope_and_returns_to_all(lab):
    first = lab.core.save_folder("Alpha")["id"]
    second = lab.core.save_folder("Beta")["id"]
    lab.seed("Alpha feed", [{"title": "Hidden article"}], first)
    lab.seed("Beta feed", [{"title": "Visible article"}], second, "/beta")
    lab.show()
    p = lab.page
    p.locator(".folder-line > button:not(.disclosure)").filter(has_text="Beta").click()
    expect(p.locator(".article-row .row-title")).to_have_text(["Visible article"])
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text("Visible article")
    p.keyboard.press("Escape")
    p.locator(".views button").filter(has_text="All articles").click()
    expect(p.locator(".article-row")).to_have_count(2)
    first_visible = p.locator(".article-row .row-title").first.inner_text()
    lab.open_title(first_visible)
    p.keyboard.press("j")
    expect(p.locator(".reading-content h1")).to_have_text(
        p.locator(".article-row .row-title").nth(1).inner_text()
    )
