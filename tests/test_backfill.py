import json
from datetime import datetime, timedelta, timezone

import pytest

from feedelio.core import Core, now
from feedelio.core.backfill import initial


def prepare(core, site, path="/rss"):
    feed = core.subscribe(site[0] + path)["id"]
    with core.db:
        core.db.execute("UPDATE jobs SET status='done'")
        core.db.execute(
            "UPDATE feeds SET next_check=?", ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),)
        )
    job = core.enqueue("backfill", {"feed_id": feed})["id"]
    return feed, job


def due(core, job):
    # Advance persisted deadlines, not the real clock or the production delay.
    with core.db:
        core.db.execute("DELETE FROM settings WHERE key='archive_next_request'")
        core.db.execute("UPDATE jobs SET result=json_remove(result,'$.not_before') WHERE id=?", (job,))


def run(core, job):
    before = core.one("SELECT * FROM jobs WHERE id=?", (job,))
    for _ in range(10):
        core.tick()
        state = core.one("SELECT * FROM jobs WHERE id=?", (job,))
        if state["result"] != before["result"] or state["status"] != before["status"]:
            return state
    raise AssertionError("Job did not run")


def json_page(routes, path, index, next_url=None):
    routes[path] = (
        200,
        {"Content-Type": "application/feed+json"},
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "Archive",
                "next_url": next_url,
                "items": [{"id": str(index), "title": f"Item {index}", "content_text": "Use <widget> here"}],
            }
        ),
    )


def test_archive_is_durable_paced_and_not_limited_to_ten_pages(core, site):
    base, routes, requests = site
    for i in range(12):
        json_page(routes, f"/pages/{i}", i, f"/pages/{i + 1}" if i < 11 else None)
    feed, job = prepare(core, site, "/pages/0")
    result = run(core, job)
    assert result["result"]["pages"] == 1
    assert len(requests) == 1  # No second fetch to discover pagination.
    for _ in range(3):
        core.tick()
    assert len(requests) == 1
    # Reopen the DB as after a worker restart; continue exactly at page two.
    with Core(core.root) as restarted:
        for _ in range(11):
            due(restarted, job)
            result = run(restarted, job)
    assert result["status"] == "done"
    assert result["result"]["pages"] == 12
    assert len(requests) == 12
    assert core.articles(feed_id=feed)["total"] == 12
    assert "<widget>" in core.article(core.articles()["items"][0]["id"])["text"]


def test_pause_resume_and_duplicate_enqueue_preserve_cursor(core, site):
    _, job = prepare(core, site)
    core.control_backfill(job, "pause")
    core.tick()
    assert not site[2]
    core.control_backfill(job, "resume")
    result = run(core, job)
    assert result["status"] == "done"


@pytest.mark.parametrize("header", ["600", "Thu, 17 Sep 2099 13:00:00 GMT"])
def test_retry_after_and_initial_failure_can_resume(core, site, header):
    _, routes, requests = site
    routes["/busy"] = (429, {"Retry-After": header}, "slow down")
    _, job = prepare(core, site, "/busy")
    result = run(core, job)
    assert result["status"] == "queued"
    assert result["result"]["attempts"] == 1
    assert (
        datetime.fromisoformat(result["result"]["not_before"]) - datetime.now(timezone.utc)
    ).total_seconds() > 590
    core.tick()
    assert len(requests) == 1
    json_page(routes, "/busy", 1)
    due(core, job)
    result = run(core, job)
    assert result["status"] == "done"
    assert result["result"]["attempts"] == 0


def test_unsupported_archive_is_explicit_and_not_retried_forever(core, site):
    _, job = prepare(core, site, "/missing")
    assert run(core, job)["status"] == "failed"
    core.tick()
    assert len(site[2]) == 1
    assert "404" in core.overview()["backfills"][0]["error"]


def test_pagination_cycle_is_stopped(core, site):
    json_page(site[1], "/cycle", 1, "/cycle")
    _, job = prepare(core, site, "/cycle")
    run(core, job)
    due(core, job)
    result = run(core, job)
    assert result["status"] == "failed"
    assert "repeats" in result["error"]
    assert len(site[2]) == 1


def test_global_archive_pacing_does_not_block_interactive_jobs(core, site):
    feed, first = prepare(core, site)
    run(core, first)
    _, second = prepare(core, site, "/json")
    rules = core.enqueue("rules")["id"]
    core.tick()
    assert core.one("SELECT status FROM jobs WHERE id=?", (rules,))["status"] == "done"
    assert core.one("SELECT status FROM jobs WHERE id=?", (second,))["status"] == "queued"
    assert len(site[2]) == 1


def test_substack_batch_then_single_articles_and_dedup(core, site):
    base, routes, requests = site
    feed, job = prepare(core, site)
    f = core.one("SELECT * FROM feeds WHERE id=?", (feed,))
    state = initial(f) | {"mode": "substack", "next_url": base + "/archive", "offset": 0}
    posts = [
        dict(
            id=i,
            canonical_url=base + f"/post/{i}",
            title=f"Post {i}",
            post_date=now(),
            audience="everyone",
            description="Summary",
        )
        for i in range(2)
    ]
    routes["/archive"] = (200, {"Content-Type": "application/json"}, json.dumps(posts))
    routes["/post/1"] = (
        200,
        {"Content-Type": "text/html"},
        "<article><p>" + "A detailed original article. " * 40 + "</p></article>",
    )
    routes["/api/v1/archive?sort=new&search=&offset=2&limit=20"] = (
        200,
        {"Content-Type": "application/json"},
        "[]",
    )
    existing = core.ingest(feed, "from-rss", "Post 0", base + "/post/0", "Keep feed body")
    core.change_articles([existing], starred=True, read=True)
    with core.db:
        core.db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(state), job))
    result = run(core, job)
    assert len(requests) == 1
    assert len(result["result"]["pending"]) == 2
    due(core, job)
    result = run(core, job)
    assert len(requests) == 2
    assert requests[-1][0] == "/post/1"
    assert core.articles()["total"] == 2
    assert core.article(existing)["starred"]
    assert core.article(existing)["body"] == "<p>Keep feed body</p>"
    due(core, job)
    result = run(core, job)
    assert result["status"] == "done"
    assert result["result"]["articles"] == 1


def test_deleted_feed_never_fetches_and_pause_survives_inflight(core, site, monkeypatch):
    feed, job = prepare(core, site)
    original = core._archive_fetch

    def fetch(*args):
        with Core(core.root) as ui:
            ui.control_backfill(job, "pause")
        return original(*args)

    monkeypatch.setattr(core, "_archive_fetch", fetch)
    assert run(core, job)["status"] == "paused"
    core.control_backfill(job, "resume")
    core.bulk_feeds([feed], "delete")
    due(core, job)
    core.tick()
    assert len(site[2]) == 1


def test_feed_page_rel_prev_archive(core, site):
    base, routes, _ = site
    routes["/paged"] = (
        200,
        {"Content-Type": "application/atom+xml"},
        f"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Archive</title><id>{base}/paged</id><updated>2026-09-17T00:00:00Z</updated><link rel="prev-archive" href="/atom"/></feed>""",
    )
    feed, _ = prepare(core, site, "/paged")
    state = core.backfill(feed)
    assert state["next_url"] == base + "/atom"


def test_existing_subscriptions_get_one_automatic_backfill(core, site):
    feed = core.subscribe(site[0] + "/rss")["id"]
    core.refresh(feed)
    for _ in range(6):
        core.tick()
    assert len(core.rows("SELECT * FROM jobs WHERE kind='backfill'")) == 1


def test_substack_adapter_selection_and_settings(core):
    f = dict(url="https://publication.substack.com/feed", options={})
    assert initial(f)["mode"] == "substack"
    assert (
        initial(f)["next_url"]
        == "https://publication.substack.com/api/v1/archive?sort=new&search=&offset=0&limit=20"
    )
    assert initial(f, "https://archive.example.com/feed")["mode"] == "feed"
    assert initial(f | {"url": "https://substack.com.evil.example/feed"})["mode"] == "feed"
    core.save_settings(dict(sidebar_width=420, list_display="title"))
    assert core.settings()["sidebar_width"] == 420
    with pytest.raises(ValueError):
        core.save_settings(dict(sidebar_width=50))
    with pytest.raises(ValueError):
        core.save_settings(dict(list_display="invalid"))


def test_retry_budget_resume_and_backfill_backup(core, site, tmp_path):
    base, routes, requests = site
    routes["/temporary"] = (503, {}, "try later")
    feed, job = prepare(core, site, "/temporary")
    for attempt in range(5):
        due(core, job)
        result = run(core, job)
        assert result["result"]["attempts"] == attempt + 1
    assert result["status"] == "failed"
    assert len(requests) == 5
    core.control_backfill(job, "resume")
    json_page(routes, "/temporary", 1, "/json")
    due(core, job)
    result = run(core, job)
    assert result["result"]["attempts"] == 0
    assert result["result"]["next_url"] == base + "/json"
    # Repeated enqueue does not create another crawler for the same feed.
    assert core.enqueue("backfill", {"feed_id": feed})["id"] == job
    with Core(tmp_path / "restored") as restored:
        restored.restore(core.backup())
        assert restored.one("SELECT * FROM jobs WHERE id=?", (job,))["result"] == result["result"]
        assert restored.rows("SELECT value FROM settings WHERE key='archive_next_request'")
        due(restored, job)
        assert run(restored, job)["status"] == "done"
        assert restored.articles()["total"] == 2


def test_site_scrapers_are_not_treated_as_archive_feeds(core, site):
    feed = core.subscribe(site[0] + "/article", options={"scrape_selector": "article"})["id"]
    core.refresh(feed)
    for _ in range(4):
        core.tick()
    assert not core.rows("SELECT * FROM jobs WHERE kind='backfill'")


@pytest.mark.parametrize("status", [404, 410])
def test_removed_archive_post_keeps_preview_and_continues(core, site, status):
    base, routes, _ = site
    feed, job = prepare(core, site)
    routes["/gone"] = (status, {}, "gone")
    state = initial(core.one("SELECT * FROM feeds WHERE id=?", (feed,)))
    state.update(
        mode="substack",
        next_url=base + "/more",
        pending=[
            dict(
                guid="gone",
                url=base + "/gone",
                title="Removed post",
                summary="Its public archive preview",
                published=now(),
                tags=[],
            )
        ],
    )
    with core.db:
        core.db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(state), job))
    result = run(core, job)
    assert result["status"] == "queued"
    assert result["result"]["next_url"] == base + "/more"
    assert result["result"]["previews"] == 1
    article = core.article(core.articles()["items"][0]["id"])
    assert article["text"] == "Its public archive preview"
    assert "archive-preview" in article["tags"]
    assert not article["extracted"]


def wordpress_page(routes, path, index):
    routes[path] = (
        200,
        {"Content-Type": "application/rss+xml"},
        f"""<rss version="2.0"><channel><title>Blog</title><generator>https://wordpress.org/?v=6.8</generator>
        <item><guid isPermaLink="false">wp-{index}</guid><title>WordPress post number {index}</title>
        <description>Body {index}</description></item></channel></rss>""",
    )


def test_wordpress_feeds_paginate_without_next_links_and_history_starts_read(core, site):
    base, routes, _ = site
    wordpress_page(routes, "/feed", 1)
    wordpress_page(routes, "/feed?paged=2", 2)
    wordpress_page(routes, "/feed?paged=3", 3)  # /feed?paged=4 is a 404, as on WordPress.
    feed = core.subscribe(base + "/feed")["id"]
    core.refresh(feed)
    assert core.articles()["total"] == 1 and core.articles()["unread"] == 0
    with core.db:
        core.db.execute("UPDATE jobs SET status='done'")
    job = core.enqueue("backfill", {"feed_id": feed})["id"]
    state = run(core, job)["result"]
    assert state["pages"] == 1 and state["next_url"] == base + "/feed?paged=2"
    for _ in range(3):
        due(core, job)
        result = run(core, job)
    assert result["status"] == "done" and result["error"] is None
    assert result["result"]["pages"] == 3 and result["result"]["articles"] == 2
    assert result["result"]["message"] == "All exposed archive pages processed."
    assert core.articles()["total"] == 3 and core.articles()["unread"] == 0
    # Only posts published after the first refresh count as unread.
    wordpress_page(routes, "/feed", 4)
    core.refresh(feed)
    assert core.articles()["unread"] == 1


def test_new_feed_read_setting_off_keeps_first_refresh_unread(core, site):
    core.save_settings({"new_feed_read": False})
    feed = core.subscribe(site[0] + "/rss")["id"]
    core.refresh(feed)
    assert core.articles()["unread"] == 1
    with pytest.raises(ValueError, match="boolean"):
        core.save_settings({"new_feed_read": "yes"})


def test_youtube_channel_archive_lists_the_video_grid_and_reads_watch_pages(core, site, monkeypatch):
    from feedelio import core as module
    from feedelio.core import backfill as archives

    feed, job = prepare(core, site)
    with core.db:  # The live feed is YouTube's, which stops at the newest 15 videos.
        core.db.execute(
            "UPDATE feeds SET url='https://www.youtube.com/feeds/videos.xml?channel_id=UCtest' WHERE id=?", (feed,)
        )
    core.ingest(feed, "yt:video:known000000", "Already here", "https://www.youtube.com/watch?v=known000000")

    def grid(id, title):
        return {"richItemRenderer": {"content": {"lockupViewModel": {
            "contentId": id, "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {"lockupMetadataViewModel": {"title": {"content": title}}},
        }}}}

    def continuation(token):
        return {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": token}}}}

    page = {"contents": {"tabs": [{"richGridRenderer": {"contents": [
        grid("known000000", "Already here"), grid("newer000000", "The newer one"), continuation("tok1"),
    ]}}]}}
    more = {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": [
        {"richItemRenderer": {"content": {"videoRenderer": {"videoId": "older000000", "title": {"runs": [{"text": "The older one"}]}}}}},
    ]}}]}
    requests = []

    def fetch(self, url, options, json=None, headers=None):
        requests.append((url, json))
        if json is None:
            assert url == "https://www.youtube.com/channel/UCtest/videos" and headers == {"Cookie": "SOCS=CAI"}
            return b"<html><script>var ytInitialData = " + module.json.dumps(page).encode() + b";</script></html>", url, {}
        assert url == archives.YOUTUBE_BROWSE and json["continuation"] == "tok1"
        return module.json.dumps(more).encode(), url, {}

    def probe(url, options=None):
        id = url.rsplit("=", 1)[1]
        return dict(
            short=id == "older000000",
            url=f"https://www.youtube.com/shorts/{id}" if id == "older000000" else url,
            duration=90,
            title=f"Title of {id}",
            description="Line one https://example.test/\n\nLine two",
            author="Channel",
            published="2021-03-04T05:06:07-07:00",
        )

    monkeypatch.setattr(Core, "_archive_fetch", fetch)
    monkeypatch.setattr(module, "probe_video", probe)
    states = []
    for _ in range(5):
        due(core, job)
        states.append(run(core, job))
        if states[-1]["status"] == "done":
            break
    assert [s["status"] for s in states] == ["queued", "queued", "queued", "done"]
    assert [r[1] is None for r in requests] == [True, False]  # One listing page, one continuation.
    titles = {a["title"]: a for a in core.articles()["items"]}
    assert set(titles) == {"Already here", "Title of newer000000", "Title of older000000"}
    older = core.article(titles["Title of older000000"]["id"])
    assert older["url"] == "https://www.youtube.com/shorts/older000000" and older["tags"] == ["short"]
    assert older["published"].startswith("2021-03-04") and older["duration"] == 90 and older["author"] == "Channel"
    assert '<a href="https://example.test/"' in older["body"] and "<p>Line two</p>" in older["body"]
    assert states[-1]["result"]["articles"] == 2
    assert states[-1]["result"]["message"] == "All exposed archive pages processed."
    assert not core.rows("SELECT 1 FROM jobs WHERE kind='extract' AND status='queued'")
    # The library's job summary counts the archive's contribution.
    queue = core.overview()["queue"]
    assert queue["backfill"]["waiting"] == 0 and queue["backfill"]["done"] == 1
    assert queue["backfill"]["articles"] == 2 and "refresh" in queue
