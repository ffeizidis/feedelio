"""Scheduled polling.

reader tracks each feed's own update interval (the ``.reader.update`` tag), so
the worker only has to ask it, on a tick, which feeds are due.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from feedelio.core import Core

log = logging.getLogger(__name__)


def poll_once(core: Core) -> None:
    """Run one polling pass over the feeds that are due."""
    core.update_feeds(scheduled=True)
    status = core.status()
    log.info(
        "poll complete: %d feeds, %d entries, %d unread, %d broken",
        status.feeds,
        status.entries,
        status.unread,
        status.broken_feeds,
    )


def poll_forever(
    core: Core,
    interval: float,
    *,
    should_continue: Callable[[], bool] = lambda: True,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll every ``interval`` seconds until ``should_continue`` says otherwise.

    A failing pass is logged and retried on the next tick rather than killing
    the worker.
    """
    while should_continue():
        try:
            poll_once(core)
        except Exception:  # a bad feed must not stop the loop
            log.exception("polling pass failed")
        sleep(interval)
