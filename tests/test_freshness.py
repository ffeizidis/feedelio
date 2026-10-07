"""New posts arrive within the hour: every feed is checked at least hourly,
"Update now" goes first, and a laptop waking from sleep costs no feed a failure."""

import time
from datetime import datetime, timedelta, timezone

import requests

from feedelio.core import EVERY_FEED, MAX_INTERVAL, Core, HostSlots, UnresolvedHost, now


def minutes_until(value):
    return (datetime.fromisoformat(value) - datetime.now(timezone.utc)).total_seconds() / 60


def post(core, feed, n, when, base):
    core.ingest(feed, f"p{n}", f"Post {n}", f"{base}/p{n}", published=when.isoformat())


def test_every_feed_is_checked_at_least_hourly_whatever_its_archive_looks_like(core, site):
    base, _, _ = site
    feed = core.subscribe(base + "/rss")["id"]
    start = datetime.now(timezone.utc)
    # A backfilled archive: posts months apart, which once meant a daily check.
    for n in range(10):
        post(core, feed, n, start - timedelta(days=90 * (n + 1)), base)
    core.refresh(feed)
    f = core.feeds()[0]
    assert f["interval"] == MAX_INTERVAL
    # Jittered, so feeds refreshed together drift apart.
    assert 0.9 * MAX_INTERVAL - 1 < minutes_until(f["next_check"]) < 1.1 * MAX_INTERVAL + 1


def test_a_feed_that_posts_often_is_checked_more_often(core, site):
    base, _, _ = site
    feed = core.subscribe(base + "/rss")["id"]
    start = datetime.now(timezone.utc)
    for n in range(6):
        post(core, feed, n, start - timedelta(minutes=40 * n), base)
    assert core.refresh_interval(feed, {}) == 20
    for n in range(6, 12):
        post(core, feed, n, start - timedelta(minutes=5 * n), base)
    assert core.refresh_interval(feed, {}) == 15  # The floor.
    assert core.refresh_interval(feed, {"interval": 240}) == 240  # A chosen interval stands.


def test_daily_intervals_from_before_are_brought_under_the_hour(tmp_path, monkeypatch):
    with Core(tmp_path) as core:
        feed = core.subscribe("https://example.org/feed")["id"]
        custom = core.subscribe("https://example.org/other", options={"interval": 240})["id"]
        with core.db:
            core.db.execute("UPDATE feeds SET interval=1440,next_check=?", (now(days=1),))
    Core.initialised.discard(tmp_path.resolve())
    with Core(tmp_path) as core:
        feeds = {f["id"]: f for f in core.feeds()}
    assert (
        feeds[feed]["interval"] == MAX_INTERVAL and minutes_until(feeds[feed]["next_check"]) <= MAX_INTERVAL
    )
    assert feeds[custom]["interval"] == 1440  # Its owner asked for this one.


def test_update_now_puts_every_feed_first_the_longest_waiting_ahead(core, site):
    base, routes, _ = site
    routes["/two"] = routes["/rss"]
    fresh = core.subscribe(base + "/rss", title="Aardvark")["id"]
    waiting = core.subscribe(base + "/two", title="Zebra")["id"]
    with core.db:
        core.db.execute("UPDATE feeds SET checked=?,next_check=? WHERE id=?", (now(), now(hours=1), fresh))
        core.db.execute(
            "UPDATE feeds SET checked=?,next_check=? WHERE id=?", (now(days=-1), now(hours=1), waiting)
        )
    core.enqueue("extract", {"article_id": "backlog"})
    core.refresh_all()
    jobs = core.rows("SELECT payload FROM jobs WHERE kind='refresh' ORDER BY updated, created")
    assert [j["payload"] for j in jobs] == [
        {"feed_id": waiting, "manual": EVERY_FEED},
        {"feed_id": fresh, "manual": EVERY_FEED},
    ]
    # A feed asked for by name still goes ahead of the library-wide pass.
    core.enqueue("refresh", {"feed_id": fresh, "manual": True})
    core.refresh_all()  # Asking for everything again does not demote it.
    assert core.tick(refreshes="only")
    assert core.one("SELECT checked FROM feeds WHERE id=?", (fresh,))["checked"] > now(minutes=-1)
    assert core.one("SELECT checked FROM feeds WHERE id=?", (waiting,))["checked"] < now(hours=-1)
    core.bulk_feeds([fresh], "refresh")  # Refreshing chosen feeds is asking now too.
    assert core.rows("SELECT payload FROM jobs WHERE kind='refresh'")[-1]["payload"]["manual"]


def test_offline_after_sleep_is_retried_in_a_minute_and_counts_against_no_feed(core, site, monkeypatch):
    base, _, _ = site
    feed = core.subscribe(base + "/rss")["id"]

    def unresolved(*_, **__):
        raise UnresolvedHost("The host could not be resolved.")

    monkeypatch.setattr("feedelio.core.Core.reader", unresolved)
    monkeypatch.setattr(Core, "network_up", lambda self, failing: False)
    assert core.refresh(feed) == {"new": 0, "offline": True}
    f = core.feeds()[0]
    assert f["failures"] == 0 and f["error"] is None and minutes_until(f["next_check"]) <= 1

    # With the network up, a host that does not resolve is the feed's problem.
    monkeypatch.setattr(Core, "network_up", lambda self, failing: True)
    try:
        core.refresh(feed)
    except UnresolvedHost:
        pass
    assert core.feeds()[0]["failures"] == 1


def test_the_network_counts_as_down_only_when_other_sites_do_not_resolve(core, monkeypatch):
    core.subscribe("https://up.example/feed")
    with core.db:
        core.db.execute("UPDATE feeds SET checked=?", (now(),))
    monkeypatch.setattr(Core, "network_checked", (0.0, True))

    def nothing_resolves(*_, **__):
        raise OSError("Temporary failure in name resolution")

    monkeypatch.setattr("socket.getaddrinfo", nothing_resolves)
    assert not core.network_up("https://down.example/feed")
    monkeypatch.setattr(Core, "network_checked", (0.0, True))
    monkeypatch.setattr("socket.getaddrinfo", lambda *_, **__: [])
    assert core.network_up("https://down.example/feed")


def test_a_publisher_asking_to_slow_down_gets_the_wait_it_named(core, site):
    base, routes, _ = site
    routes["/busy"] = (429, {"Retry-After": "7200"}, "slow down")
    feed = core.subscribe(base + "/busy")["id"]
    try:
        core.refresh(feed)
    except Exception:
        pass
    assert minutes_until(core.feeds()[0]["next_check"]) > 110


def test_a_feed_moves_onto_the_address_of_its_deleted_duplicate(core, site):
    base, routes, _ = site
    routes["/moved"] = (301, {"Location": "/rss"}, "")
    duplicate = core.subscribe(base + "/rss")["id"]
    core.refresh(duplicate)
    core.bulk_feeds([duplicate], "delete")
    feed = core.subscribe(base + "/moved")["id"]
    core.refresh(feed)
    urls = {f["id"]: f["url"] for f in core.rows("SELECT id,url FROM feeds")}
    assert urls == {feed: base + "/rss", duplicate: base + "/moved"}
    assert core.feeds()[0]["error"] is None
    core.refresh(feed)  # Now fetched at its new address directly.
    assert core.feeds()[0]["error"] is None


def test_new_counts_only_articles_that_were_added(core, site):
    base, _, _ = site
    feed = core.subscribe(base + "/rss")["id"]
    assert core.refresh(feed)["new"] == 1
    with core.db:  # The stored copy differs, so the next pass rewrites it.
        core.db.execute("UPDATE articles SET title='Old title'")
    assert core.refresh(feed)["new"] == 0


def test_a_future_publish_date_does_not_pin_an_article_to_the_top(core, site):
    base, _, _ = site
    feed = core.subscribe(base + "/rss")["id"]
    ahead = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    core.ingest(feed, "soon", "Scheduled post", base + "/soon", published=ahead)
    stored = core.articles()["items"][0]
    assert abs(minutes_until(stored["published"])) < 1
    core.ingest(feed, "soon", "Scheduled post", base + "/soon", published=ahead, body="<p>Edited.</p>")
    assert core.articles()["items"][0]["published"] == stored["published"]


def test_refreshes_run_at_most_two_at_a_time_per_site(core, site):
    base, routes, _ = site
    routes["/two"] = routes["/rss"]
    for path in ("/rss", "/two"):
        core.enqueue("refresh", {"feed_id": core.subscribe(base + path)["id"]})
    slots = HostSlots(limit=1)
    assert slots.take("127.0.0.1")  # Another thread is fetching from this site.
    assert not core.tick(refreshes="only", slots=slots)
    slots.release("127.0.0.1")
    assert core.tick(refreshes="only", slots=slots)
    assert slots.busy["127.0.0.1"] == 0  # Released once the refresh finished.
    # The main thread leaves refreshes to the refresh threads.
    while core.tick(refreshes=False):
        pass
    assert core.rows("SELECT count(*) n FROM jobs WHERE kind='refresh' AND status='queued'")[0]["n"] == 1


def test_connection_errors_count_as_unreachable():
    from feedelio.core import unreachable

    try:
        try:
            raise requests.exceptions.ConnectionError("refused")
        except Exception as inner:
            raise ValueError("while getting feed") from inner
    except ValueError as outer:
        assert unreachable(outer)
    assert not unreachable(ValueError("bad HTTP status code"))


def test_the_worker_refreshes_many_feeds_side_by_side_without_locking_up(core, site):
    import threading

    from feedelio.worker import REFRESH_THREADS, loop

    base, routes, _ = site
    feeds = []
    for n in range(60):
        routes[f"/f{n}"] = routes["/rss"]
        feeds.append(core.subscribe(f"{base}/f{n}", title=f"Feed {n}")["id"])
    core.refresh_all()
    stop, slots = threading.Event(), HostSlots()
    threads = [
        threading.Thread(target=loop, args=(stop,), kwargs=dict(refreshes="only", slots=slots))
        for _ in range(REFRESH_THREADS)
    ] + [threading.Thread(target=loop, args=(stop,), kwargs=dict(refreshes=False))]  # Archives, alongside.
    for thread in threads:
        thread.start()
    deadline = datetime.now() + timedelta(seconds=30)
    while datetime.now() < deadline:
        left = core.one("SELECT count(*) n FROM jobs WHERE kind='refresh' AND status IN ('queued','running')")
        if not left["n"]:
            break
    stop.set()
    for thread in threads:
        thread.join(10)
    assert not left["n"]
    assert not core.rows("SELECT error FROM jobs WHERE status='failed' AND error LIKE '%locked%'")
    assert all(f["checked"] and not f["error"] for f in core.feeds())


def test_offline_the_scheduler_waits_for_the_network_instead_of_every_feed(core, site, monkeypatch):
    base, _, _ = site
    core.subscribe(base + "/rss")
    with core.db:  # Due, with nothing queued for it yet.
        core.db.execute("DELETE FROM jobs")
        core.db.execute("UPDATE feeds SET next_check=NULL")
    refreshes = lambda: len(core.rows("SELECT id FROM jobs WHERE kind='refresh'"))  # noqa: E731
    # A check has just found the network down, and it is still down.
    monkeypatch.setattr(Core, "network_checked", (time.monotonic(), False))
    core.schedule()
    assert refreshes() == 0
    # Looked at again once the half minute is up, it is back.
    monkeypatch.setattr(Core, "network_checked", (time.monotonic() - 31, False))
    monkeypatch.setattr("socket.getaddrinfo", lambda *_, **__: [])
    with core.db:
        core.db.execute("UPDATE feeds SET checked=?", (now(),))
    core.schedule()
    assert refreshes() == 1
