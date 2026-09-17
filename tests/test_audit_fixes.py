"""Regression checks for the failures reproduced in the browser audit."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from feedelio.api import app
from feedelio.core.content import local_frame_origin, sanitize


@pytest.mark.parametrize("path", ["/rss", "/atom", "/rdf", "/json"])
def test_discovery_validates_direct_feeds(core, site, path):
    assert core.discover(site[0] + path)[0]["url"] == site[0] + path
    assert core.feeds() == []


@pytest.mark.parametrize("mime", ["text/html", "application/rss+xml", "application/feed+json"])
def test_discovery_rejects_feedless_pages_even_with_feed_mime(core, site, mime):
    site[1]["/not-a-feed"] = (200, {"Content-Type": mime}, "<html><h1>No feeds here</h1></html>")
    with pytest.raises(ValueError, match="No feed found"):
        core.discover(site[0] + "/not-a-feed")
    assert core.feeds() == []


@pytest.mark.parametrize(
    "invalid",
    [
        '<outline text="Bad" xmlUrl="file:///bad"/>',
        '<outline text="   "/>',
        "<outline>" * 51 + "</outline>" * 51,
    ],
)
def test_opml_rolls_back_all_tables_on_late_failure(core, site, invalid):
    core.subscribe(site[0] + "/rss", title="Keep me")
    before = {table: core.rows(f"SELECT * FROM {table}") for table in ("feeds", "folders", "jobs")}
    xml = f'''<opml><body><outline text="New folder">
        <outline text="Valid first" xmlUrl="{site[0]}/json"/>
        </outline>{invalid}</body></opml>'''
    with pytest.raises(ValueError):
        core.import_opml(xml)
    for table, rows in before.items():
        assert core.rows(f"SELECT * FROM {table}") == rows


@pytest.mark.parametrize(
    "style",
    [
        "width:1px;height:1px",
        "WIDTH: 1PX !important",
        "max-height: .5px",
        "width:0",
        r"w\69 dth:/*comment*/1px",
        r"display:n\6f ne",
        "visibility: HIDDEN",
    ],
)
def test_css_pixels_are_removed_but_normal_images_survive(style):
    body = sanitize(
        f'<img src="/pixel" style="{style}"><img src="/photo" style="width:300px;height:200px">',
        "https://example.org",
    )
    assert "/pixel" not in body
    assert 'src="https://example.org/photo"' in body


def test_duplicate_survivors_follow_delete_and_undo(core, site):
    feeds, ids = [], []
    for i in range(3):
        feed = core.subscribe(site[0] + f"/feed-{i}")["id"]
        feeds.append(feed)
        ids.append(core.ingest(feed, str(i), "The same syndicated story", site[0] + f"/story-{i}"))

    def visible():
        return [a["id"] for a in core.articles()["items"]]

    assert visible() == ids[:1]
    undo_feed = core.bulk_feeds([feeds[0]], "delete")["undo"]
    assert visible() == ids[1:2]
    undo_article = core.change_articles([ids[1]], deleted=True)["undo"]
    assert visible() == ids[2:]
    core.undo(undo_article)
    assert visible() == ids[1:2]
    core.undo(undo_feed)
    assert visible() == ids[:1]
    assert core.articles(deduplicate=False)["total"] == 3


def test_history_is_last_open_order_not_mark_read_order(core, site):
    feed = core.subscribe(site[0] + "/rss")["id"]
    ids = [core.ingest(feed, str(i), f"Story {i}", site[0] + f"/story-{i}") for i in range(3)]
    core.mark_all()
    assert core.articles(view="history")["total"] == 0
    for id in (ids[0], ids[1], ids[0]):
        core.change_articles([id], read=True, opened=True)
    core.change_articles([ids[1], ids[2]], read=True)
    assert [a["id"] for a in core.articles(view="history")["items"]] == [ids[0], ids[1]]


def test_interval_changes_schedule_immediately_in_both_directions(core, site):
    feed = core.subscribe(site[0] + "/rss")["id"]
    start = datetime.now(timezone.utc)
    for i in range(3):
        core.ingest(
            feed,
            str(i),
            f"Story {i}",
            site[0] + f"/story-{i}",
            published=(start - timedelta(hours=i * 4)).isoformat(),
        )
    core.edit_feed(feed, options={"interval": 17})
    manual = core.feeds()[0]
    assert manual["interval"] == 17
    assert 16 * 60 < (datetime.fromisoformat(manual["next_check"]) - start).total_seconds() < 18 * 60
    core.edit_feed(feed, title="Only rename")
    assert core.feeds()[0]["next_check"] == manual["next_check"]
    core.edit_feed(feed, options={"interval": None})
    automatic = core.feeds()[0]
    assert automatic["interval"] == 120
    assert 119 * 60 < (datetime.fromisoformat(automatic["next_check"]) - start).total_seconds() < 121 * 60


def test_download_metadata_identifies_each_enclosure(core, site):
    feed = core.subscribe(site[0] + "/rss")["id"]
    article = core.ingest(feed, "episode", "Two audio tracks", site[0] + "/episode")
    urls = [site[0] + "/first.wav", site[0] + "/second.wav"]
    jobs = [core.enqueue("download", {"article_id": article, "url": url})["id"] for url in urls]
    assert {d["id"]: d["url"] for d in core.article(article)["downloads"]} == dict(zip(jobs, urls))


def test_csp_only_adds_the_configured_local_http_origin(core):
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert "frame-src https:;" in client.get("/").headers["Content-Security-Policy"]
        core.save_settings({"invidious": "http://127.0.0.1:9876/player"})
        policy = client.get("/").headers["Content-Security-Policy"]
        assert "frame-src https: http://127.0.0.1:9876;" in policy
        assert "frame-src https: http:;" not in policy
        core.save_settings({"invidious": "https://video.example.org"})
        assert "frame-src https:;" in client.get("/").headers["Content-Security-Policy"]


@pytest.mark.parametrize(
    "url", ["http://video.example.org", "http://localhost:pass@evil.org", "http://localhost;unsafe"]
)
def test_local_frame_origin_rejects_broad_or_injected_http_sources(url):
    with pytest.raises(ValueError):
        local_frame_origin(url)
