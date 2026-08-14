"""Request-scoped access to the core service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from anyio.to_thread import run_sync
from fastapi import Depends, Request

from feedelio.core import Core


def get_core(request: Request) -> Core:
    """The single :class:`Core` created for the app's lifetime."""
    core: Core = request.app.state.core
    return core


CoreDep = Annotated[Core, Depends(get_core)]


async def offload[T](fn: Callable[[], T]) -> T:
    """Run a blocking core call off the event loop.

    reader is synchronous; every core call from a route goes through here.
    """
    result: T = await run_sync(fn)
    return result
