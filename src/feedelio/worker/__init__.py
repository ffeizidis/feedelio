"""Polling worker: a separate process that keeps the library fresh."""

from feedelio.worker.poller import poll_forever, poll_once

__all__ = ["poll_forever", "poll_once"]
