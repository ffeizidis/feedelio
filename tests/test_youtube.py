"""Saved YouTube playlists: a folder of their own, kept even after YouTube loses the video."""

import pytest

from feedelio.core import PLAYLIST, YOUTUBE


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
