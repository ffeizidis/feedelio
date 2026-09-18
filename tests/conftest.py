import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from feedelio.core import Core


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", "1")
    monkeypatch.setenv("FEEDELIO_DATA", str(tmp_path))
    with Core(tmp_path) as core:
        yield core


@pytest.fixture
def site():
    routes = {}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            status, headers, body = routes.get(self.path, (404, {}, "not found"))
            if headers.get("ETag") and self.headers.get("If-None-Match") == headers["ETag"]:
                status, body = 304, ""
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    routes["/rss"] = (
        200,
        {"Content-Type": "application/rss+xml", "ETag": '"feed-v1"'},
        f"""<rss version="2.0"><channel><title>Test journal</title><link>{base}</link><description>A feed</description>
        <item><guid isPermaLink="false">one</guid><title>A slower and more thoughtful internet</title><link>{base}/article?utm_source=rss</link>
        <description><![CDATA[<p>Independent publishing and a quiet reading ritual.</p>]]></description></item></channel></rss>""",
    )
    routes["/json"] = (
        200,
        {"Content-Type": "application/feed+json"},
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "JSON journal",
                "items": [
                    {
                        "id": "json-one",
                        "title": "JSON story",
                        "url": base + "/json-story",
                        "content_text": "Typed text, safely parsed.",
                    }
                ],
            }
        ),
    )
    routes["/atom"] = (
        200,
        {"Content-Type": "application/atom+xml"},
        f'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom journal</title><id>{base}/atom</id><updated>2026-09-15T12:00:00Z</updated><entry><id>atom-one</id><title>Atom story</title><updated>2026-09-15T12:00:00Z</updated><link href="{base}/atom-story"/><content type="html">&lt;p&gt;Atom article body.&lt;/p&gt;</content></entry></feed>''',
    )
    routes["/rdf"] = (
        200,
        {"Content-Type": "application/rdf+xml"},
        f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"><channel rdf:about="{base}/rdf"><title>RSS one</title><link>{base}</link><description>RDF feed</description></channel><item rdf:about="{base}/rdf-story"><title>RDF story</title><link>{base}/rdf-story</link><description>RSS 1.0 body</description></item></rdf:RDF>''',
    )
    routes["/article"] = (
        200,
        {"Content-Type": "text/html"},
        f'''<html><head><title>A full story</title><link rel="canonical" href="{base}/canonical?utm_medium=amp"/><script type="application/ld+json">{{"isAccessibleForFree":false}}</script></head><body><article><h1>A full story</h1><p>Longform reading has a rhythm all its own. A slow morning gives us room for curiosity and attention.</p><p>Quasar observatories give scientists a different perspective on the universe.</p></article><div class="transcript">Supplied transcript about photosynthesis.</div></body></html>''',
    )
    routes["/home"] = (
        200,
        {"Content-Type": "text/html"},
        '<html><head><link rel="alternate" type="application/rss+xml" title="Journal" href="/rss"/></head></html>',
    )
    yield base, routes, requests
    server.shutdown()
    server.server_close()
    thread.join()
