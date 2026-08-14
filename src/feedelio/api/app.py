"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from feedelio import __version__
from feedelio.api.errors import install_error_handlers
from feedelio.api.routes import router
from feedelio.config import Settings, get_settings
from feedelio.core import Core, make_core


def create_app(settings: Settings | None = None, *, core: Core | None = None) -> FastAPI:
    """Build the ASGI app.

    Passing ``core`` hands the app an already-open service (tests do this);
    otherwise one is opened for the app's lifetime from ``settings``.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = core is None
        service = core or make_core(settings)
        app.state.core = service
        try:
            yield
        finally:
            if owned:
                service.close()

    app = FastAPI(title="Feedelio", version=__version__, lifespan=lifespan)
    app.include_router(router)
    install_error_handlers(app)
    _serve_spa(app, settings.static_dir)
    return app


def _serve_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA from ``static_dir`` when it has been built into the image."""
    index = static_dir / "index.html"
    if not index.is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        """Serve built assets; unknown paths fall back to the SPA entry point."""
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
