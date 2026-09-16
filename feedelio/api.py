"""Thin HTTP adapter; application behavior belongs to feedelio.core."""

import hashlib
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from feedelio.core import Core

app = FastAPI(title="Feedelio", docs_url="/api/docs", openapi_url="/api/openapi.json")


class Command(BaseModel):
    payload: dict = Field(default_factory=dict)


def session_token(token):
    return hmac.new(token.encode(), b"feedelio-session-v1", hashlib.sha256).hexdigest()


@app.middleware("http")
async def protect(request: Request, call_next):
    token = os.getenv("FEEDELIO_TOKEN", "")
    path = request.url.path
    protected = path.startswith("/api/") and path not in ("/api/login", "/api/health")
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
    authenticated = bool(token) and (
        hmac.compare_digest(bearer, token)
        or hmac.compare_digest(request.cookies.get("feedelio_session", ""), session_token(token))
    )
    local_host = request.url.hostname in ("localhost", "127.0.0.1", "::1")
    if protected and not token and not local_host:
        return JSONResponse(
            {"detail": "Set FEEDELIO_TOKEN before accessing Feedelio through a remote hostname."},
            status_code=403,
        )
    if protected and token and not authenticated:
        return JSONResponse({"detail": "Sign in with your Feedelio access token."}, status_code=401)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            extension = origin.startswith("chrome-extension://") and (
                authenticated or not token and local_host
            )
            if not extension:
                return JSONResponse({"detail": "Cross-origin requests are disabled."}, status_code=403)
    length = int(request.headers.get("content-length", "0"))
    if length > int(os.getenv("FEEDELIO_MAX_IMPORT_MB", "2048")) * 1024 * 1024:
        return JSONResponse({"detail": "Request too large."}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: http: data:; media-src 'self' https: http:; frame-src https:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def invalid(request, error):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.exception_handler(TypeError)
async def bad_arguments(request, error):
    return JSONResponse({"detail": "Invalid command arguments: " + str(error)}, status_code=422)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/login")
def login(command: Command, request: Request):
    token = os.getenv("FEEDELIO_TOKEN", "")
    if token and not hmac.compare_digest(str(command.payload.get("token", "")), token):
        raise HTTPException(401, "Incorrect access token.")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "feedelio_session",
        session_token(token),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.get("/api/overview")
def overview():
    with Core() as core:
        return core.overview()


@app.get("/api/articles")
def articles(
    view: str = "all",
    feed_id: str | None = None,
    folder_id: str | None = None,
    unread: bool = False,
    q: str = "",
    tag: str | None = None,
    sort: str = "newest",
    offset: int = 0,
    limit: int = 100,
    deduplicate: bool = True,
):
    with Core() as core:
        return core.articles(**locals_without_core(locals()))


def locals_without_core(values):
    return {k: v for k, v in values.items() if k != "core"}


@app.get("/api/articles/{id}")
def article(id: str):
    with Core() as core:
        return core.article(id)


@app.post("/api/actions/{action}")
def action(action: str, command: Command):
    with Core() as core:
        return core.execute(action, command.payload)


@app.get("/api/rules")
def rules():
    with Core() as core:
        return core.rules()


@app.get("/api/statistics")
def statistics():
    with Core() as core:
        return core.statistics()


@app.get("/api/export/{format}")
def export(format: str):
    with Core() as core:
        if format == "opml":
            return Response(
                core.export_opml(),
                media_type="application/xml",
                headers={"Content-Disposition": 'attachment; filename="feedelio.opml"'},
            )
        if format == "json":
            return JSONResponse(
                core.backup(), headers={"Content-Disposition": 'attachment; filename="feedelio.json"'}
            )
        raise HTTPException(404)


@app.get("/api/articles/{id}/obsidian")
def obsidian(id: str):
    with Core() as core:
        return Response(
            core.obsidian(id),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="feedelio-{id}.md"'},
        )


@app.get("/api/downloads/{id}")
def download(id: str):
    with Core() as core:
        return FileResponse(core.download_path(id), media_type="audio/mpeg")


static = Path(os.getenv("FEEDELIO_STATIC", "web/dist"))
if static.is_dir():
    app.mount("/", StaticFiles(directory=static, html=True), name="web")
