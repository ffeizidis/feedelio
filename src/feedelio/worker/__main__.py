"""Entry point: ``python -m feedelio.worker`` (or ``feedelio-worker``)."""

from __future__ import annotations

import logging

from feedelio.config import get_settings
from feedelio.core import make_core
from feedelio.worker.poller import poll_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    with make_core(settings) as core:
        poll_forever(core, settings.poll_interval)


if __name__ == "__main__":
    main()
