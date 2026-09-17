"""Black-box browser audit infrastructure; no mocks of the application or worker."""

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from feedelio.core import Core

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "test-results" / "adversarial"
RESULTS = []


def pytest_configure(config):
    config.addinivalue_line("markers", "features(*numbers): requested feature numbers exercised")
    config.addinivalue_line("markers", "protected: start an isolated token-protected server")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    result = outcome.get_result()
    if result.when == "call" or result.failed:
        RESULTS.append(
            {
                "test": item.name,
                "features": list(item.get_closest_marker("features").args),
                "outcome": result.outcome,
                "phase": result.when,
                "seconds": result.duration,
                "failure": str(result.longrepr) if result.failed else "",
            }
        )


def pytest_sessionfinish(session, exitstatus):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "results.json").write_text(json.dumps(RESULTS, indent=2))


@pytest.fixture(scope="session")
def playwright():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright):
    browser = playwright.chromium.launch()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "environment.json").write_text(
        json.dumps({"chromium": browser.version, "python": sys.version, "viewport": [1440, 1000]}, indent=2)
    )
    yield browser
    browser.close()


class Lab:
    def __init__(self, base, publisher, routes, requests, core, page, env, logs):
        self.base, self.publisher, self.routes, self.requests = base, publisher, routes, requests
        self.core, self.page, self.env, self.logs = core, page, env, logs
        self.worker = None

    def api(self, path):
        response = self.page.request.get(self.base + "/api/" + path)
        assert response.ok, response.text()
        return response.json()

    def command(self, name, payload):
        response = self.page.request.post(self.base + "/api/actions/" + name, data={"payload": payload})
        assert response.ok, response.text()
        return response.json()

    def until(self, fn, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = fn()
            if value:
                return value
            self.page.wait_for_timeout(200)
        raise AssertionError(f"Condition not satisfied within {timeout}s")

    def start_worker(self):
        if self.worker is None:
            self.worker = subprocess.Popen(
                [sys.executable, "-m", "feedelio.worker"],
                cwd=ROOT,
                env=self.env,
                stdout=self.logs,
                stderr=self.logs,
            )

    def finish_jobs(self):
        self.start_worker()
        self.until(
            lambda: not any(j["status"] in ("queued", "running") for j in self.api("overview")["jobs"])
        )

    def show(self):
        self.page.goto(self.base)
        expect(self.page.locator(".brand")).to_be_visible()

    def manager(self, tab):
        # A persisted subscription can be visible to API polling before its
        # success handler finishes closing the add dialog and refreshing queries.
        expect(self.page.locator("dialog:not(:has(.dialog-tabs))")).to_have_count(0)
        if not self.page.locator("dialog").count():
            self.page.get_by_role("button", name="Manage library", exact=True).click()
        self.page.locator(".dialog-tabs").get_by_role("button", name=tab, exact=True).click()

    def close(self):
        self.page.get_by_role("button", name="Close dialog", exact=True).click()

    def upload(self, xml):
        self.manager("Data & connections")
        self.page.locator('input[accept=".opml,.xml"]').set_input_files(
            {"name": "audit.opml", "mimeType": "application/xml", "buffer": xml.encode()}
        )

    def rss(self, path, title, entries, extra_headers=None):
        items = "".join(
            f'<item><guid isPermaLink="false">{escape(e.get("guid", str(i)))}</guid>'
            f"<title>{escape(e['title'])}</title><link>{escape(e.get('url', self.publisher + '/article/' + str(i)))}</link>"
            f"<description><![CDATA[{e.get('body', '<p>Ordinary article body.</p>')}]]></description>"
            f"{e.get('extra', '')}</item>"
            for i, e in enumerate(entries)
        )
        self.routes[path] = (
            200,
            {"Content-Type": "application/rss+xml", **(extra_headers or {})},
            f'<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><title>{escape(title)}</title><link>{self.publisher}</link><description>Audit publisher</description>{items}</channel></rss>',
        )

    def seed(self, title="Audit feed", articles=None, folder="inbox", path="/feed"):
        self.rss(path, title, articles or [])
        feed = self.core.subscribe(self.publisher + path, folder, title)["id"]
        for i, a in enumerate(articles or []):
            self.core.ingest(
                feed,
                a.get("guid", str(i)),
                a["title"],
                a.get("url", self.publisher + "/article/" + str(i)),
                a.get("body", "<p>Ordinary article body.</p>"),
                published=a.get("published"),
                enclosures=a.get("enclosures"),
                transcript=a.get("transcript", ""),
            )
        # Seeded reading scenarios don't wait for a network update. Ingestion scenarios use UI subscription.
        with self.core.db:
            self.core.db.execute("UPDATE jobs SET status='done' WHERE kind='refresh'")
            self.core.db.execute(
                "UPDATE feeds SET next_check=?",
                ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),),
            )
        return feed

    def open_title(self, title):
        self.page.locator(".article-row").filter(
            has=self.page.get_by_role("heading", name=title, exact=True)
        ).click()
        expect(self.page.locator(".reading-content > h1")).to_have_text(title)


@pytest.fixture
def lab(tmp_path, request, browser):
    directory = ARTIFACTS / request.node.name
    directory.mkdir(parents=True, exist_ok=True)
    routes, requests = {}, []

    class Publisher(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"path": self.path, "headers": dict(self.headers)})
            status, headers, body = routes.get(self.path, (404, {"Content-Type": "text/plain"}, "Not found"))
            if callable(body):
                status, headers, body = body(self)
            if headers.get("ETag") == self.headers.get("If-None-Match") and headers.get("ETag"):
                status, body = 304, ""
            elif headers.get("Last-Modified") == self.headers.get("If-Modified-Since") and headers.get(
                "Last-Modified"
            ):
                status, body = 304, ""
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Publisher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    publisher = f"http://127.0.0.1:{server.server_port}"
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 8000)
    routes["/episode.wav"] = (200, {"Content-Type": "audio/wav"}, audio.getvalue())
    routes["/favicon.ico"] = (
        200,
        {"Content-Type": "image/svg+xml"},
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="green"/></svg>',
    )
    routes["/picture.svg"] = routes["/favicon.ico"]
    env = {
        **os.environ,
        "FEEDELIO_DATA": str(tmp_path / "data"),
        "FEEDELIO_ALLOW_PRIVATE_NETWORK": "1",
        "FEEDELIO_TOKEN": "",
    }
    if request.node.get_closest_marker("protected"):
        env["FEEDELIO_TOKEN"] = "isolated-audit-token"
    core = Core(env["FEEDELIO_DATA"])
    # Subscription writes validate URLs before the separate worker uses the opt-in environment.
    prior = os.environ.get("FEEDELIO_ALLOW_PRIVATE_NETWORK")
    os.environ["FEEDELIO_ALLOW_PRIVATE_NETWORK"] = "1"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    logs = (directory / "server.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "feedelio.api:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=logs,
        stderr=logs,
    )
    with httpx.Client(timeout=1) as client:
        for _ in range(100):
            try:
                if client.get(base + "/api/health").is_success:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Audit API did not start")
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(5000)
    errors = []
    console = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on(
        "console",
        lambda m: (
            console.append({"type": m.type, "text": m.text}) if m.type in ("warning", "error") else None
        ),
    )
    instance = Lab(base, publisher, routes, requests, core, page, env, logs)
    yield instance
    page.screenshot(path=str(directory / "final.png"), full_page=True)
    (directory / "accessibility.txt").write_text(page.locator("body").aria_snapshot())
    (directory / "console-errors.json").write_text(json.dumps(errors, indent=2))
    (directory / "browser-console.json").write_text(json.dumps(console, indent=2))
    (directory / "publisher-requests.json").write_text(json.dumps(requests, indent=2))
    context.tracing.stop(path=str(directory / "trace.zip"))
    context.close()
    for child in (instance.worker, process):
        if child:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    core.db.close()
    logs.close()
    server.shutdown()
    server.server_close()
    thread.join()
    if prior is None:
        os.environ.pop("FEEDELIO_ALLOW_PRIVATE_NETWORK", None)
    else:
        os.environ["FEEDELIO_ALLOW_PRIVATE_NETWORK"] = prior
