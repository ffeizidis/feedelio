"""Core domain layer: everything Feedelio knows how to do, independent of HTTP.

The API and the worker both go through this layer; neither talks to
:mod:`reader` directly.
"""

from feedelio.core.service import (
    Core,
    EntryInfo,
    FeedError,
    FeedExistsError,
    FeedInfo,
    FeedUnavailableError,
    Status,
    make_core,
)

__all__ = [
    "Core",
    "EntryInfo",
    "FeedError",
    "FeedExistsError",
    "FeedInfo",
    "FeedUnavailableError",
    "Status",
    "make_core",
]
