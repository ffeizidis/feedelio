import pytest

from feedelio.core import Core


def test_appearance_defaults_validation_and_roundtrip(core):
    assert core.settings()["list_display"] == "title"
    assert core.settings()["theme"] == "dark"
    values = dict(
        list_width=640,
        sidebar_width=220,
        accent="violet",
        feed_colours=False,
        unread_marker="dot",
        wordmark="plain",
        reduce_motion=True,
        open_titles_in="original",
        list_display="compact",
        font="newsreader",
    )
    core.save_settings(values)
    with Core(core.root) as other:
        assert all(other.settings()[k] == v for k, v in values.items())
    for invalid in (
        {"list_width": 299},
        {"list_width": 1001},
        {"accent": "bad"},
        {"unread_marker": "bad"},
        {"wordmark": "bad"},
        {"feed_colours": "false"},
        {"reduce_motion": 1},
        {"open_titles_in": "bad"},
    ):
        with pytest.raises(ValueError):
            core.save_settings(invalid)


def test_feed_hues_persist_across_rename_delete_restore_and_legacy(core, site):
    base = site[0]
    first = core.subscribe(base + "/first", title="Zed")["id"]
    second = core.subscribe(base + "/second", title="Alpha")["id"]
    assert {f["id"]: f["options"]["hue"] for f in core.feeds()} == {first: 0, second: 1}
    core.edit_feed(first, title="Aardvark", options={"interval": 30})
    deletion = core.bulk_feeds([first], "delete")
    assert core.feeds()[0]["options"]["hue"] == 1
    third = core.subscribe(base + "/third")["id"]
    assert next(f for f in core.feeds() if f["id"] == third)["options"]["hue"] == 2
    core.undo(deletion["undo"])
    with core.db:
        core.db.execute("UPDATE feeds SET options=json_remove(options,'$.hue') WHERE id=?", (first,))
    Core.initialised.discard(core.root)  # Migrations run once per process; simulate a restart.
    with Core(core.root) as migrated:
        assert next(f for f in migrated.feeds() if f["id"] == first)["options"]["hue"] == 0
    assert next(f for f in core.feeds() if f["id"] == second)["options"]["hue"] == 1


def test_title_search_and_mark_all_share_scope_and_starred_export(core, site):
    feed = core.subscribe(site[0] + "/test")["id"]
    title = core.ingest(feed, "title", "Attention in the everyday", site[0] + "/1", "<p>Short body</p>")
    body = core.ingest(feed, "body", "A different story", site[0] + "/2", "<p>Attention in the body</p>")
    assert core.articles(q="attention")["total"] == 2
    assert core.articles(q="attention", titles_only=True)["total"] == 1
    assert core.articles(q='" OR title:* --', titles_only=True)["total"] == 0
    core.mark_all(q="attention", titles_only=True)
    assert core.article(title)["read"] and not core.article(body)["read"]
    assert core.articles()["unread"] == 1
    core.change_articles([title], starred=True)
    assert [a["id"] for a in core.export_starred()["articles"]] == [title]
    core.change_articles([title], deleted=True)
    assert core.export_starred()["articles"] == []
