"""The one place core exceptions become HTTP status codes.

Core stays HTTP-free, so the translation has to live somewhere in the API
layer; putting it here rather than in each route means a new route gets the
right status without remembering anything, and a status can only be changed in
one place. Routes raise nothing: they let the core exception travel.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from feedelio.core import (
    FeedError,
    FeedExistsError,
    FeedNotFoundError,
    FeedUnavailableError,
    FolderError,
    FolderExistsError,
    FolderNotFoundError,
    InvalidFolderNameError,
)

log = logging.getLogger(__name__)

#: Core exception -> status. Subclasses inherit their nearest mapped ancestor,
#: which is what the per-route ``except`` blocks used to give us.
STATUS_BY_ERROR: dict[type[Exception], int] = {
    FeedExistsError: status.HTTP_409_CONFLICT,
    FeedNotFoundError: status.HTTP_404_NOT_FOUND,
    FeedUnavailableError: status.HTTP_400_BAD_REQUEST,
    FolderExistsError: status.HTTP_409_CONFLICT,
    FolderNotFoundError: status.HTTP_404_NOT_FOUND,
    InvalidFolderNameError: status.HTTP_400_BAD_REQUEST,
}


def install_error_handlers(app: FastAPI) -> None:
    """Teach ``app`` to answer core exceptions the way it answers HTTPException."""
    for base in (FeedError, FolderError):
        app.add_exception_handler(base, _core_error_handler)


async def _core_error_handler(request: Request, exc: Exception) -> Response:
    """Render a core exception in FastAPI's own error shape."""
    for cls in type(exc).__mro__:
        if (code := STATUS_BY_ERROR.get(cls)) is not None:
            return JSONResponse({"detail": str(exc)}, status_code=code)

    # An error nobody mapped is a bug in this file, not something the client
    # did: say so honestly with a 500 and keep the details in the log.
    log.exception("Unmapped core error from %s", request.url.path, exc_info=exc)
    return JSONResponse(
        {"detail": "Internal Server Error"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
    )
