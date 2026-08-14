"""JSON API routes.

Milestone 0 ships only the health endpoint; feed and entry routes arrive with
the core reading loop.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from feedelio import __version__
from feedelio.api.deps import CoreDep, offload

router = APIRouter(prefix="/api")


class Health(BaseModel):
    """Liveness plus the counts that prove the database is readable."""

    status: str
    version: str
    feeds: int
    entries: int
    unread: int
    broken_feeds: int


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
