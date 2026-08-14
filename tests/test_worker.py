"""The polling loop must keep running when a pass fails."""

from __future__ import annotations

from pathlib import Path

import pytest

from feedelio.core import Core
from feedelio.worker import poll_forever, poll_once


def test_poll_once_fetches_due_feeds(core: Core) -> None:
    core.reader.add_feed("sample.atom")
    poll_once(core)
    assert core.status().entries == 2


def test_poll_forever_sleeps_between_passes(loaded_core: Core) -> None:
    slept: list[float] = []
    ticks = iter([True, True, False])
    poll_forever(
        loaded_core,
        interval=1.5,
        should_continue=lambda: next(ticks),
        sleep=slept.append,
    )
    assert slept == [1.5, 1.5]


def test_a_failing_pass_does_not_stop_the_loop(
    loaded_core: Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def boom(**_: object) -> None:
        calls["n"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr(loaded_core.reader, "update_feeds", boom)
    ticks = iter([True, True, False])
    poll_forever(
        loaded_core,
        interval=0,
        should_continue=lambda: next(ticks),
        sleep=lambda _: None,
    )
    assert calls["n"] == 2


def test_main_polls_until_interrupted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from feedelio.worker import __main__ as entry

    seen: list[float] = []

    def fake_poll_forever(core: Core, interval: float) -> None:
        seen.append(interval)

    monkeypatch.setenv("FEEDELIO_DB_PATH", str(tmp_path) + "/worker.sqlite")
    monkeypatch.setenv("FEEDELIO_POLL_INTERVAL", "5")
    monkeypatch.setattr(entry, "poll_forever", fake_poll_forever)
    entry.main()
    assert seen == [5.0]
