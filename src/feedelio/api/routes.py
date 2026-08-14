"""JSON API routes.

Thin by design: every route hands a blocking core call to :func:`offload` and
reshapes the result into a response model. No :mod:`reader` here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from feedelio import __version__
from feedelio.api.deps import CoreDep, offload
from feedelio.core import FeedExistsError, FeedUnavailableError

router = APIRouter(prefix="/api")

#: Most articles anyone can usefully scroll in one response.
MAX_ENTRIES = 500


class Health(BaseModel):
    """Liveness plus the counts that prove the database is readable."""

    status: str
    version: str
    feeds: int
    entries: int
    unread: int
    broken_feeds: int


class NewFeed(BaseModel):
    """A subscription request."""

    url: Annotated[str, Field(min_length=1)]


class Feed(BaseModel):
    """A subscription."""

    model_config = ConfigDict(from_attributes=True)

    url: str
    title: str | None
    link: str | None
    updated: datetime | None
    version: str | None
    broken: bool


class Entry(BaseModel):
    """An article. ``(feed_url, id)`` identifies it; ids are per-feed."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    feed_url: str
    feed_title: str | None
    title: str | None
    link: str | None
    author: str | None
    published: datetime | None
    updated: datetime | None
    content: str | None
    read: bool
    important: bool


@router.get("/health")
async def health(core: CoreDep) -> Health:
    """Report that the app is up and the library is queryable."""
    status = await offload(core.status)
    return Health(
        status="ok",
        version=__version__,
        feeds=status.feeds,
        entries=status.entries,
        unread=status.unread,
        broken_feeds=status.broken_feeds,
    )


@router.post("/feeds", status_code=status.HTTP_201_CREATED)
async def subscribe(new: NewFeed, core: CoreDep) -> Feed:
    """Subscribe to a feed and fetch it immediately."""
    try:
        feed = await offload(lambda: core.subscribe(new.url))
    except FeedExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except FeedUnavailableError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return Feed.model_validate(feed)


@router.get("/feeds")
async def list_feeds(core: CoreDep) -> list[Feed]:
    """Every subscription, by title."""
    feeds = await offload(core.list_feeds)
    return [Feed.model_validate(feed) for feed in feeds]


@router.get("/entries")
async def list_entries(
    core: CoreDep,
    feed: Annotated[str | None, Query(description="Limit to one feed URL.")] = None,
    read: Annotated[bool | None, Query(description="Filter by read state.")] = None,
    important: Annotated[bool | None, Query(description="Filter by star.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_ENTRIES)] = 100,
) -> list[Entry]:
    """Articles, newest first."""
    entries = await offload(
        lambda: core.list_entries(feed=feed, read=read, important=important, limit=limit)
    )
    return [Entry.model_validate(entry) for entry in entries]
