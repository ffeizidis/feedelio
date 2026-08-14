"""Core domain layer: everything Feedelio knows how to do, independent of HTTP.

The API and the worker both go through this layer; neither talks to
:mod:`reader` directly.
"""

from feedelio.core.service import (
    UNFILED,
    Core,
    EntryInfo,
    FeedError,
    FeedExistsError,
    FeedInfo,
    FeedNotFoundError,
    FeedUnavailableError,
    FolderError,
    FolderExistsError,
    FolderInfo,
    FolderNotFoundError,
    ImportSummary,
    InvalidFolderNameError,
    OpmlError,
    OpmlExport,
    Status,
    make_core,
)

__all__ = [
    "UNFILED",
    "Core",
    "EntryInfo",
    "FeedError",
    "FeedExistsError",
    "FeedInfo",
    "FeedNotFoundError",
    "FeedUnavailableError",
    "FolderError",
    "FolderExistsError",
    "FolderInfo",
    "FolderNotFoundError",
    "ImportSummary",
    "InvalidFolderNameError",
    "OpmlError",
    "OpmlExport",
    "Status",
    "make_core",
]
