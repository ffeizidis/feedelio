"""Thin HTTP adapter; application behavior belongs to feedelio.core."""

import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from feedelio.core import Core

app = FastAPI(title="Feedelio", docs_url="/api/docs", openapi_url="/api/openapi.json")


class Command(BaseModel):
    payload: dict = Field(default_factory=dict)


def session_token(token):
    return hmac.new(token.encode(), b"feedelio-session-v1", hashlib.sha256).hexdigest()


def access_token():
    return "" if os.getenv("FEEDELIO_NO_AUTH") == "1" else os.getenv("FEEDELIO_TOKEN", "")


@app.middleware("http")
async def protect(request: Request, call_next):
    token = access_token()
    path = request.url.path
    protected = path.startswith("/api/") and path not in ("/api/login", "/api/health")
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
    authenticated = bool(token) and (
        hmac.compare_digest(bearer, token)
        or hmac.compare_digest(request.cookies.get("feedelio_session", ""), session_token(token))
    )
    local_host = request.url.hostname in ("localhost", "127.0.0.1", "::1")
    try:
        local_peer = bool(request.client) and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local_peer = False
    # Explicit opt-in for loopback-published Docker, whose peer is a bridge address.
    # Network isolation is the operator's responsibility in no-login mode.
    local_access = local_host and (local_peer or os.getenv("FEEDELIO_NO_AUTH") == "1")
    if protected and not token and not local_access:
        return JSONResponse(
            {"detail": "Set FEEDELIO_TOKEN for non-loopback access, including Docker and reverse proxies."},
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
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
    if length > int(os.getenv("FEEDELIO_MAX_IMPORT_MB", "2048")) * 1024 * 1024:
        return JSONResponse({"detail": "Request too large."}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    frames = Core.frame_cache.get(Core.data_root())
    if frames is None:  # Cached per process; only settings changes recompute it.
        with Core() as core:
            frames = core.frame_sources()
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: http: data:; media-src 'self' https: http:; "
        f"frame-src {frames}; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
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
    token = access_token()
    if not token:
        return JSONResponse({"ok": True})
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
    titles_only: bool = False,
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


# Read-only, and deliberately not actions: a keystroke-debounced check must not
# travel the action path that invalidates the whole client cache.
@app.get("/api/rules/preview")
def rules_preview():
    with Core() as core:
        return core.preview_rules()


@app.post("/api/rules/validate")
def rules_validate(command: Command):
    with Core() as core:
        return core.validate_rule(**command.payload)


@app.get("/api/statistics")
def statistics():
    with Core() as core:
        return core.statistics()


def attachment(filename):
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@app.get("/api/export/{format}")
def export(format: str):
    with Core() as core:
        if format == "opml":
            return Response(
                core.export_opml(), media_type="application/xml", headers=attachment("feedelio.opml")
            )
        if format == "json":
            return JSONResponse(core.backup(), headers=attachment("feedelio.json"))
        if format == "starred":
            return JSONResponse(core.export_starred(), headers=attachment("feedelio-starred.json"))
        raise HTTPException(404)


@app.get("/api/articles/{id}/obsidian")
def obsidian(id: str):
    with Core() as core:
        return Response(
            core.obsidian(id),
            media_type="text/markdown",
            headers=attachment(f"feedelio-{id}.md"),
        )


@app.get("/api/downloads/{id}")
def download(id: str):
    with Core() as core:
        return FileResponse(core.download_path(id), media_type="audio/mpeg")


class SinglePage(StaticFiles):
    """The app names its pages in the address (/f/<feed>, /a/<article>, …); they
    are all the one page, so a reload of any of them gets it."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


static = Path(os.getenv("FEEDELIO_STATIC", "web/dist"))
if static.is_dir():
    app.mount("/", SinglePage(directory=static, html=True), name="web")
