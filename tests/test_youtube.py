"""Saved YouTube playlists: a folder of their own, kept even after YouTube loses the video."""

import json

import pytest
from conftest import local_client
from fastapi.testclient import TestClient

from feedelio.api import app
from feedelio.core import PLAYLIST, YOUTUBE, Core, content, now


def video(n, **extra):
    return {"id": f"video{n:06d}", "title": f"Video {n}", "channel": "A channel", **extra}


def playlist(id, title, *videos):
    return {"id": id, "title": title, "videos": list(videos)}


def saved(core, feed_id):
    return core.rows(
        "SELECT guid,title,author,meta,deleted FROM articles WHERE feed_id=? ORDER BY json_extract(meta,'$.position')",
        (PLAYLIST + feed_id,),
    )


def test_a_sync_gives_each_playlist_a_feed_in_the_youtube_folder(core):
    result = core.youtube_sync(
        [playlist("WL", "Watch later", video(1), video(2)), playlist("PLmusic", "Music", video(3))]
    )
    assert result == {"playlists": 2, "new": 3}
    playlists = core.overview()["playlists"]
    assert {(p["id"], p["title"], p["folder_id"], p["unread"]) for p in playlists} == {
        ("yt-WL", "Watch later", YOUTUBE, 2),
        ("yt-PLmusic", "Music", YOUTUBE, 1),
    }
    first = saved(core, "WL")[0]
    assert first["title"] == "Video 1" and first["author"] == "A channel"
    assert first["meta"] == {"video_id": "video000001", "position": 0}
    assert core.articles(view="youtube")["total"] == 3
    assert core.articles(feed_id="yt-PLmusic")["total"] == 1
    assert core.overview()["counts"]["youtube"] == 3
    # The same sync again changes nothing.
    assert core.youtube_sync([playlist("WL", "Watch later", video(1), video(2))])["new"] == 0


def test_saved_videos_stay_out_of_the_subscriptions(core, site):
    base, _, _ = site
    core.subscribe(base + "/rss")
    core.refresh(core.feeds()[0]["id"])
    core.youtube_sync([playlist("WL", "Watch later", video(1))])
    overview = core.overview()
    assert [f["url"] for f in overview["feeds"]] == [base + "/rss"]
    assert YOUTUBE not in {f["id"] for f in overview["folders"]}
    assert overview["counts"]["total"] == 1 and overview["counts"]["youtube"] == 1
    assert core.articles()["total"] == 1
    assert "youtube" not in core.export_opml()
    core.refresh_all()
    assert not core.rows("SELECT 1 FROM jobs WHERE json_extract(payload,'$.feed_id') LIKE 'yt-%'")
    with pytest.raises(ValueError, match="YouTube"):
        core.delete_folder(YOUTUBE)
    with pytest.raises(ValueError, match="YouTube"):
        core.save_folder("Inside", parent_id=YOUTUBE)


def test_a_renamed_playlist_follows_youtube(core):
    core.youtube_sync([playlist("PLx", "Old name", video(1))])
    core.youtube_sync([playlist("PLx", "New name", video(1))])
    assert core.overview()["playlists"][0]["title"] == "New name"


def test_a_video_that_leaves_its_playlist_is_kept_and_marked(core):
    core.youtube_sync([playlist("PLx", "List", video(1), video(2))])
    core.youtube_sync([playlist("PLx", "List", video(2))])
    gone, kept = sorted(saved(core, "PLx"), key=lambda a: a["guid"])
    assert not gone["deleted"] and "left_playlist" in gone["meta"]
    assert "left_playlist" not in kept["meta"] and kept["meta"]["position"] == 0
    core.youtube_sync([playlist("PLx", "List", video(1), video(2))])  # Added back.
    assert all("left_playlist" not in a["meta"] for a in saved(core, "PLx"))


def test_a_playlist_read_only_in_part_marks_nothing_as_left(core):
    core.youtube_sync([playlist("PLx", "List", video(1), video(2))])
    core.youtube_sync([dict(playlist("PLx", "List", video(2)), partial=True)])
    assert all("left_playlist" not in a["meta"] for a in saved(core, "PLx"))


def test_a_video_youtube_took_down_keeps_what_was_stored(core):
    core.youtube_sync([playlist("PLx", "List", video(1))])
    taken_down = video(1, title="[Deleted video]", channel="", unavailable=True)
    core.youtube_sync([playlist("PLx", "List", taken_down, video(2, unavailable=True))])
    rows = saved(core, "PLx")
    assert len(rows) == 1  # One never seen while available has nothing to keep.
    assert rows[0]["title"] == "Video 1" and rows[0]["author"] == "A channel"
    assert "unavailable" in rows[0]["meta"]


def test_what_is_deleted_here_stays_deleted(core):
    core.youtube_sync([playlist("PLx", "List", video(1), video(2)), playlist("PLy", "Other", video(3))])
    first = core.rows("SELECT id FROM articles WHERE guid='yt:video:video000001'")[0]["id"]
    core.change_articles([first], deleted=True)
    core.bulk_feeds(["yt-PLy"], "delete")
    core.youtube_sync([playlist("PLx", "List", video(1), video(2)), playlist("PLy", "Other", video(3))])
    assert [a["deleted"] for a in saved(core, "PLx")] == [1, 0]
    assert [p["id"] for p in core.overview()["playlists"]] == ["yt-PLx"]


def test_rules_leave_saved_videos_alone(core):
    core.save_rule(name="Skip", field="title", pattern="Video", action="drop", is_regex=False)
    core.youtube_sync([playlist("PLx", "List", video(1))])
    assert core.articles(view="youtube")["total"] == 1


def test_bad_ids_are_ignored(core):
    result = core.youtube_sync(
        [playlist("../x", "Bad"), playlist("PLx", "List", {"id": "short"}, {"id": "../../etc/pas"})]
    )
    assert result == {"playlists": 1, "new": 0}


JPEG = b"\xff\xd8\xff\xe0" + b"thumbnail" * 50


def watch_page(base, n, captions=True):
    player = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {
            "videoId": f"video{n:06d}",
            "author": "The channel",
            "channelId": "UCchannel",
            "keywords": ["cooking", "bread"],
            "shortDescription": f"About video {n}.\nhttps://example.org/recipe",
            "thumbnail": {
                "thumbnails": [
                    {"url": f"{base}/small.jpg", "width": 120},
                    {"url": f"{base}/big.jpg", "width": 1280},
                ]
            },
        },
        "microformat": {"playerMicroformatRenderer": {"category": "Howto & Style"}},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"baseUrl": f"{base}/captions-auto", "kind": "asr"},
                    {"baseUrl": f"{base}/captions"},
                ]
                if captions
                else []
            }
        },
    }
    chapters = [
        {"chapterRenderer": {"title": {"simpleText": title}, "timeRangeStartMillis": start}}
        for title, start in (("Intro", 0), ("Dough", 65000), ("Baking", 600000))
    ]
    return f"""<html><head>
    <meta itemprop="name" content="Real title {n}">
    <meta itemprop="duration" content="PT12M5S">
    <meta itemprop="datePublished" content="2024-05-01T10:00:00-07:00">
    </head><body><script>var ytInitialPlayerResponse = {json.dumps(player)};</script>
    <script>var ytInitialData = {json.dumps({"markers": chapters})};</script></body></html>"""


GONE_PAGE = """<html><script>var ytInitialPlayerResponse = {"playabilityStatus":
    {"status": "ERROR", "reason": "This video has been removed by the uploader"}};</script></html>"""


@pytest.fixture
def youtube(site, monkeypatch):
    base, routes, requests = site
    monkeypatch.setattr(content, "YOUTUBE", base)
    for n in (1, 2):
        routes[f"/watch?v=video{n:06d}"] = (200, {"Content-Type": "text/html"}, watch_page(base, n))
    routes["/big.jpg"] = (200, {"Content-Type": "image/jpeg"}, JPEG)
    routes["/captions"] = (
        200,
        {"Content-Type": "text/xml"},
        "<transcript><text>Knead the dough.</text></transcript>",
    )
    routes["/captions-auto"] = (
        200,
        {"Content-Type": "text/xml"},
        "<transcript><text>uh knead</text></transcript>",
    )
    return base, routes, requests


def run_jobs(core):
    while core.tick(refreshes=False):
        pass


def test_a_saved_video_is_preserved_with_its_details(core, youtube):
    core.youtube_sync([playlist("PLx", "List", video(1))])
    run_jobs(core)
    a = core.rows("SELECT * FROM articles")[0]
    assert a["title"] == "Real title 1" and a["author"] == "The channel"
    assert a["published"].startswith("2024-05-01T17:00") and a["duration"] == 725
    assert "About video 1." in a["body"] and 'href="https://example.org/recipe"' in a["body"]
    assert a["transcript"] == "Knead the dough."  # Written captions over automatic ones.
    meta = a["meta"]
    assert meta["keywords"] == ["cooking", "bread"] and meta["category"] == "Howto & Style"
    assert meta["chapters"] == [
        {"start": 0, "title": "Intro"},
        {"start": 65, "title": "Dough"},
        {"start": 600, "title": "Baking"},
    ]
    assert meta["channel_url"] == "https://www.youtube.com/channel/UCchannel"
    assert meta["thumbnail"] and (core.root / "thumbs" / "video000001.jpg").read_bytes() == JPEG
    assert core.articles(view="youtube")["items"][0]["meta"]["thumbnail"]
    assert core.articles(view="youtube", q="Knead")["total"] == 1  # The transcript is searchable.


def test_a_video_youtube_removes_later_keeps_everything(core, youtube):
    base, routes, _ = youtube
    core.youtube_sync([playlist("PLx", "List", video(1))])
    run_jobs(core)
    before = core.rows("SELECT title,body,transcript,duration,meta FROM articles")[0]
    routes["/watch?v=video000001"] = (200, {"Content-Type": "text/html"}, GONE_PAGE)
    core.preserve_video("video000001")
    after = core.rows("SELECT title,body,transcript,duration,meta FROM articles")[0]
    assert "unavailable" in after["meta"]
    assert {k: after[k] for k in ("title", "body", "transcript", "duration")} == {
        k: before[k] for k in ("title", "body", "transcript", "duration")
    }
    assert after["meta"]["chapters"] == before["meta"]["chapters"]
    del routes["/watch?v=video000001"]  # A 404 is gone too.
    core.preserve_video("video000001")
    assert core.rows("SELECT title FROM articles")[0]["title"] == "Real title 1"


def test_a_page_that_says_nothing_is_tried_again_not_taken_as_gone(core, youtube):
    _, routes, _ = youtube
    core.youtube_sync([playlist("PLx", "List", video(1))])
    routes["/watch?v=video000001"] = (200, {"Content-Type": "text/html"}, "<html>Before you continue</html>")
    with pytest.raises(ValueError, match="tried again"):
        core.preserve_video("video000001")
    assert "unavailable" not in core.rows("SELECT meta FROM articles")[0]["meta"]


def test_a_video_in_two_playlists_is_fetched_once(core, youtube):
    _, _, requests = youtube
    core.youtube_sync([playlist("PLx", "List", video(1)), playlist("PLy", "Other", video(1))])
    run_jobs(core)
    assert sum(path == "/watch?v=video000001" for path, _ in requests) == 1
    assert [a["title"] for a in core.rows("SELECT title FROM articles")] == ["Real title 1"] * 2


def test_saved_videos_are_looked_at_again_weekly(core, youtube):
    core.youtube_sync([playlist("PLx", "List", video(1), video(2))])
    run_jobs(core)
    with core.db:
        core.db.execute("UPDATE articles SET meta=json_set(meta,'$.checked',?)", (now(days=-8),))
        core.db.execute("DELETE FROM settings WHERE key='youtube_checked'")
    core.schedule()
    queued = core.rows("SELECT payload FROM jobs WHERE kind='video' AND status='queued'")
    assert sorted(j["payload"]["video_id"] for j in queued) == ["video000001", "video000002"]
    core.schedule()  # Not again within the hour.
    assert len(core.rows("SELECT 1 FROM jobs WHERE kind='video' AND status='queued'")) == 2


def test_thumbnails_are_served_privately_and_backed_up(core, youtube, tmp_path_factory, monkeypatch):
    core.youtube_sync([playlist("PLx", "List", video(1))])
    run_jobs(core)
    with local_client() as client:
        response = client.get("/api/thumbs/video000001")
        assert response.status_code == 200 and response.content == JPEG
        assert client.get("/api/thumbs/video000002").status_code == 404
        assert client.get("/api/thumbs/..%2Fapp.sqlite").status_code in (400, 404)
    monkeypatch.setenv("FEEDELIO_TOKEN", "private-test-token")
    with TestClient(app) as stranger:
        assert stranger.get("/api/thumbs/video000001").status_code == 401
    backup = core.backup()
    other = tmp_path_factory.mktemp("restored")
    with Core(other) as restored:
        restored.restore(json.loads(json.dumps(backup)))
        assert restored.thumbnail_path("video000001").read_bytes() == JPEG
        assert restored.overview()["playlists"][0]["title"] == "List"
        assert restored.archive_size()["bytes"] >= len(JPEG)


def test_chapters_from_the_description_when_youtube_lists_none():
    text = "Recipe\n0:00 Intro\n1:05 - Dough\n1:02:03 Baking\nThanks"
    assert content.description_chapters(text) == [
        {"start": 0, "title": "Intro"},
        {"start": 65, "title": "Dough"},
        {"start": 3723, "title": "Baking"},
    ]
    assert content.description_chapters("1:00 Not from the start\n2:00 b\n3:00 c") == []
    assert content.description_chapters("0:00 a\n1:00 b") == []  # Fewer than three.
