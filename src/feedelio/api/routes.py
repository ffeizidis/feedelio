"""JSON API routes.

Thin by design: every route hands a blocking core call to :func:`offload` and
reshapes the result into a response model. No :mod:`reader` here.

Core exceptions are left to travel: :mod:`feedelio.api.errors` maps them to a
status once, for every route.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from feedelio import __version__
from feedelio.api.deps import CoreDep, offload

router = APIRouter(prefix="/api")

#: Most articles anyone can usefully scroll in one response.
MAX_ENTRIES = 500

#: Largest subscription list we will read. A thousand feeds is a few hundred
#: kilobytes; anything past this is not somebody's Inoreader export.
MAX_OPML_BYTES = 4 * 1024 * 1024


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


class Folder(BaseModel):
    """A folder with the feeds in it, as the sidebar renders them.

    ``name`` is empty for the group of feeds that are in no folder, so the
    client can pass any folder's ``name`` straight back as ``?folder=``.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    feeds: list[Feed]


class FolderName(BaseModel):
    """A folder name, for creating and renaming."""

    name: str


class FeedFolder(BaseModel):
    """Where a feed should live. An empty ``folder`` takes it out of all of them."""

    url: Annotated[str, Field(min_length=1)]
    folder: str


class OpmlImport(BaseModel):
    """What an upload did — silence after a 400-feed import would be cruel."""

    model_config = ConfigDict(from_attributes=True)

    added: int
    already_present: int
    failed: list[str]
    folders_created: list[str]
    folders_skipped: list[str]


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
    feed = await offload(lambda: core.subscribe(new.url))
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
    folder: Annotated[
        str | None, Query(description="Limit to one folder; empty for the unfiled feeds.")
    ] = None,
    read: Annotated[bool | None, Query(description="Filter by read state.")] = None,
    important: Annotated[bool | None, Query(description="Filter by star.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_ENTRIES)] = 100,
) -> list[Entry]:
    """Articles, newest first. A folder reads as one merged stream."""
    entries = await offload(
        lambda: core.list_entries(
            feed=feed, folder=folder, read=read, important=important, limit=limit
        )
    )
    return [Entry.model_validate(entry) for entry in entries]


@router.get("/folders")
async def list_folders(core: CoreDep) -> list[Folder]:
    """Every folder with its feeds; the unfiled feeds come last."""
    folders = await offload(core.list_folders)
    return [Folder.model_validate(folder) for folder in folders]


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(new: FolderName, core: CoreDep) -> Folder:
    """Add an empty folder."""
    folder = await offload(lambda: core.create_folder(new.name))
    return Folder.model_validate(folder)


@router.patch("/folders/{name}")
async def rename_folder(name: str, renamed: FolderName, core: CoreDep) -> Folder:
    """Rename a folder; its feeds go with it."""
    folder = await offload(lambda: core.rename_folder(name, renamed.name))
    return Folder.model_validate(folder)


@router.delete("/folders/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(name: str, core: CoreDep) -> None:
    """Drop a folder. Its feeds stay subscribed, unfiled."""
    await offload(lambda: core.delete_folder(name))


@router.put("/feeds/folder", status_code=status.HTTP_204_NO_CONTENT)
async def move_feed(move: FeedFolder, core: CoreDep) -> None:
    """Move a feed into a folder, or out of every folder."""
    await offload(lambda: core.move_feed(move.url, move.folder))


@router.post("/opml")
async def import_opml(
    core: CoreDep,
    file: Annotated[UploadFile, File(description="An OPML subscription list.")],
) -> OpmlImport:
    """Import a subscription list, its categories becoming folders.

    The feeds are not fetched here — an export of that size would keep the
    request open for minutes; the worker collects them.
    """
    # Read one byte past the limit and no further: the multipart parser has
    # already received the body, but this bounds what we pull out of it. A cap
    # on the upload itself belongs in front of the app, not here.
    content = await file.read(MAX_OPML_BYTES + 1)
    if len(content) > MAX_OPML_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"a subscription list larger than {MAX_OPML_BYTES} bytes is not one we read",
        )
    summary = await offload(lambda: core.import_opml(content))
    return OpmlImport.model_validate(summary)


@router.get("/opml", response_class=Response, responses={200: {"content": {"application/xml": {}}}})
async def export_opml(core: CoreDep) -> Response:
    """Download the whole library as an OPML file, folders included."""
    export = await offload(core.export_opml)
    return Response(
        content=export.content,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )
