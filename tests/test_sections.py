"""Folders for videos and podcasts: pages of their own, and the feeds offered to them."""

import pytest

from feedelio.core import Core

PODCAST = """<rss version="2.0"><channel><title>Talk show</title><link>{base}</link>
<item><guid>e1</guid><title>Episode one</title><link>{base}/e1</link>
<enclosure url="{base}/e1.mp3" type="audio/mpeg" length="1"/></item>
<item><guid>e2</guid><title>Episode two</title><link>{base}/e2</link>
<enclosure url="{base}/e2.m4a?x=1" type="" length="1"/></item></channel></rss>"""
VIDEOS = """<rss version="2.0"><channel><title>Clips</title><link>{base}</link>
<item><guid>v1</guid><title>Clip one</title><link>https://vimeo.com/123</link></item>
<item><guid>v2</guid><title>Clip two</title><link>{base}/v2</link>
<enclosure url="{base}/v2.mp4" type="video/mp4" length="1"/></item></channel></rss>"""


def test_folders_named_for_videos_and_podcasts_get_their_pages(tmp_path):
    with Core(tmp_path) as core:
        videos = core.save_folder("> Videos")["id"]
        nested = core.save_folder("Essays", parent_id=videos)["id"]
        podcasts = core.save_folder("Podcasts")["id"]
        inner = core.save_folder("Podcasts", parent_id=core.save_folder("Reading")["id"])["id"]
        core.db.execute("ALTER TABLE folders DROP COLUMN kind")
        core.db.commit()
    Core.initialised.discard(Core.data_root(tmp_path))
    with Core(tmp_path) as core:
        section = {f["id"]: f["section"] for f in core.folders()}
    assert (section[videos], section[nested], section[podcasts]) == ("videos", "videos", "podcasts")
    assert section[inner] == section["inbox"] == ""  # Only a top-level folder makes a page.


def test_a_page_keeps_its_feeds_out_of_all_articles(core, site):
    base, routes, _ = site
    routes["/podcast"] = (200, {"Content-Type": "application/rss+xml"}, PODCAST.format(base=base))
    shows = core.save_folder("Shows", section="podcasts")["id"]
    talk = core.save_folder("Talk", parent_id=shows)["id"]
    core.refresh(core.subscribe(base + "/podcast", folder_id=talk)["id"])
    core.refresh(core.subscribe(base + "/rss")["id"])
    titles = lambda **kw: [a["title"] for a in core.articles(**kw)["items"]]  # noqa: E731
    assert titles() == ["A slower and more thoughtful internet"]
    assert sorted(titles(view="podcasts")) == ["Episode one", "Episode two"]
    assert titles(view="podcasts", folder_id=talk) == titles(view="all", folder_id=shows)
    assert titles(view="videos") == []
    counts = core.overview()["counts"]
    assert (counts["total"], counts["videos"]) == (1, 0)
    assert counts["podcasts"] == core.articles(view="podcasts", unread=True)["total"]
    # A subfolder moved out takes its feeds back to All articles.
    core.save_folder("Talk", id=talk)
    assert titles(view="podcasts") == []
    assert len(titles()) == 3


def test_only_a_top_level_folder_chooses_its_page(core):
    shows = core.save_folder("Shows", section="podcasts")["id"]
    core.save_folder("Shows renamed", id=shows)  # Saving without a page keeps it.
    child = core.save_folder("Child", parent_id=shows, section="videos")["id"]
    section = {f["id"]: (f["kind"], f["section"]) for f in core.folders()}
    assert section[shows] == ("podcasts", "podcasts")
    assert section[child] == ("", "podcasts")
    core.save_folder("Shows renamed", id=shows, section="")
    assert {f["id"]: f["section"] for f in core.folders()}[child] == ""
    with pytest.raises(ValueError, match="articles, videos or podcasts"):
        core.save_folder("Odd", section="music")


def test_discovery_says_what_each_feed_carries(core, site):
    base, routes, _ = site
    routes["/podcast"] = (200, {"Content-Type": "application/rss+xml"}, PODCAST.format(base=base))
    routes["/clips"] = (200, {"Content-Type": "application/rss+xml"}, VIDEOS.format(base=base))
    routes["/page"] = (
        200,
        {"Content-Type": "text/html"},
        """<html><head>
        <link rel="alternate" type="application/rss+xml" title="Audio" href="/podcast"/>
        <link rel="alternate" type="application/rss+xml" title="Video" href="/clips"/>
        <link rel="alternate" type="application/rss+xml" title="Text" href="/rss"/>
        <link rel="alternate" type="application/rss+xml" title="Gone" href="/missing"/></head></html>""",
    )
    assert [(f["title"], f["kind"]) for f in core.discover(base + "/page")] == [
        ("Audio", "podcasts"),
        ("Video", "videos"),
        ("Text", ""),
        ("Gone", ""),
    ]
    assert core.discover(base + "/podcast")[0]["kind"] == "podcasts"


def test_older_folder_rows_still_restore(core, tmp_path):
    backup = core.backup()
    for f in backup["tables"]["folders"]:
        f.pop("kind")
    with Core(tmp_path / "other") as other:
        other.restore(backup)
        assert {f["section"] for f in other.folders()} == {""}


def test_a_youtube_channel_page_is_found_past_the_consent_page(core, site, monkeypatch):
    base, routes, requests = site
    monkeypatch.setattr("feedelio.core.is_youtube", lambda url: url.startswith(base))
    routes["/@films"] = (
        200,
        {"Content-Type": "text/html"},
        '<html><head><meta property="og:title" content="Films">'
        '<script>var ytInitialData = {"metadata":{"externalId":"UCabcdefghijklmnopqrstuv"}}</script></head></html>',
    )
    assert core.discover(base + "/@films") == [
        {
            "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCabcdefghijklmnopqrstuv",
            "title": "Films",
            "kind": "videos",
        }
    ]
    assert requests[-1][1]["Cookie"] == "SOCS=CAI"
