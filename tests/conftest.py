"""Shared fixtures.

Every test gets its own SQLite database and reads feeds from
``tests/fixtures`` instead of the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from feedelio.config import Settings
from feedelio.core import Core, make_core

FIXTURES = Path(__file__).parent / "fixtures"

#: Path of the sample feed, relative to ``feed_root``.
SAMPLE_FEED = "sample.atom"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "feedelio.sqlite",
        static_dir=tmp_path / "static",
        feed_root=str(FIXTURES),
    )


@pytest.fixture
def core(settings: Settings) -> Iterator[Core]:
    with make_core(settings) as service:
        yield service


@pytest.fixture
def loaded_core(core: Core) -> Core:
    """A core with the sample feed subscribed and fetched."""
    core.reader.add_feed(SAMPLE_FEED)
    core.update_feeds(scheduled=False)
    return core
