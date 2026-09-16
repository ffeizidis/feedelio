"""Content processing uses maintained parsers, sanitizers, extractors and matchers."""

import hashlib
import html
import ipaddress
import json
import os
import socket
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import nh3
import regex
import trafilatura
from bs4 import BeautifulSoup


def safe_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an http(s) URL without embedded credentials.")
    if os.getenv("FEEDELIO_ALLOW_PRIVATE_NETWORK") != "1":
        for info in socket.getaddrinfo(
            parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        ):
            if not ipaddress.ip_address(info[4][0]).is_global:
                raise ValueError(
                    "Private network addresses are disabled. Set FEEDELIO_ALLOW_PRIVATE_NETWORK=1 to opt in."
                )
    return urlunsplit(parts)


def clean_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def fetch(url: str, options=None, max_bytes=8_000_000):
    options = options or {}
    headers = {"User-Agent": options.get("user_agent") or "Feedelio/0.1 (+private feed reader)"}
    origin = urlsplit(url).netloc
    if options.get("cookie"):
        headers["Cookie"] = options["cookie"]
    with httpx.Client(timeout=30, proxy=options.get("proxy") or None, follow_redirects=False) as client:
        for _ in range(10):
            safe_url(url)
            if urlsplit(url).netloc != origin:
                headers.pop("Cookie", None)
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError("Response exceeds the configured size limit.")
                return bytes(data), str(response.url), response.headers
    raise ValueError("Too many redirects.")


def sanitize(body: str, base: str) -> str:
    soup = BeautifulSoup(body or "", "html.parser")
    for tag in soup.select("script,style,iframe,object,embed,form,input,button,link,meta,svg"):
        tag.decompose()
    for img in soup.select("img"):
        tiny = any(
            str(img.get(k, "")).removesuffix("px").isdigit() and int(str(img.get(k)).removesuffix("px")) <= 2
            for k in ("width", "height")
        )
        if tiny or regex.search(r"display\s*:\s*none|visibility\s*:\s*hidden", img.get("style", ""), regex.I):
            img.decompose()
            continue
        img.attrs.pop("srcset", None)
        img["loading"] = "lazy"
        img["referrerpolicy"] = "no-referrer"
    for node in soup.find_all(True):
        for attr in ("href", "src", "poster"):
            if node.get(attr):
                url = clean_url(urljoin(base, node[attr]))
                if urlsplit(url).scheme not in ("http", "https", "mailto"):
                    del node[attr]
                else:
                    node[attr] = url
    return nh3.clean(
        str(soup),
        attributes={
            "*": {"title"},
            "a": {"href"},
            "img": {"src", "alt", "width", "height", "loading", "referrerpolicy"},
            "td": {"colspan", "rowspan"},
            "th": {"colspan", "rowspan"},
        },
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"},
    )


def plain(body):
    return BeautifulSoup(body or "", "html.parser").get_text(" ", strip=True)


def fingerprint(title, text):
    return hashlib.sha256((title.strip().lower() + "\n" + text.strip()).encode()).hexdigest()


def extract(url, options=None):
    options = options or {}
    data, final, _ = fetch(url, options)
    soup = BeautifulSoup(data, "html.parser")
    canonical = soup.select_one('link[rel~="canonical"][href]')
    final = clean_url(urljoin(final, canonical["href"])) if canonical else clean_url(final)
    if urlsplit(final).scheme not in ("http", "https"):
        final = clean_url(url)
    selector = options.get("selector")
    if selector:
        selected = soup.select(selector)
        if not selected:
            raise ValueError("The configured article CSS selector matched no elements.")
        body = "".join(map(str, selected))
    else:
        body = trafilatura.extract(
            data, output_format="html", include_links=True, include_images=True, include_tables=True
        )
    transcript = ""
    if options.get("transcript_selector"):
        transcript = "\n".join(
            node.get_text(" ", strip=True) for node in soup.select(options["transcript_selector"])
        )
    track = soup.select_one(
        'track[kind="captions"][src], track[kind="subtitles"][src], a[rel="transcript"][href]'
    )
    if track and not transcript:
        raw, _, _ = fetch(urljoin(final, track.get("src") or track.get("href")), options)
        transcript = plain(raw.decode("utf-8", errors="replace"))
    elif embed_url(url) and not transcript:
        # Use caption tracks already supplied by the source player; never run ASR.
        document = data.decode("utf-8", errors="replace")
        match = regex.search(r'"captionTracks"\s*:\s*', document)
        if match:
            tracks, _ = json.JSONDecoder().raw_decode(document[match.end() :])
            if tracks and tracks[0].get("baseUrl"):
                raw, _, _ = fetch(tracks[0]["baseUrl"], options)
                transcript = plain(raw.decode("utf-8", errors="replace"))
    if not body:
        if transcript:
            body = text_html(soup.title.get_text() if soup.title else "Video transcript")
        else:
            raise ValueError("No article body or provided transcript could be extracted from this page.")
    paywall = False
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            paywall = paywall or '"isAccessibleForFree": false' in json.dumps(json.loads(node.get_text()))
        except (ValueError, TypeError):
            pass
    return {
        "body": sanitize(body, final),
        "url": final,
        "transcript": transcript,
        "paywall": paywall,
        "title": soup.title.get_text(strip=True) if soup.title else final,
    }


def embed_url(url, invidious=""):
    parts = urlsplit(url)
    video = None
    if parts.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        video = dict(parse_qsl(parts.query)).get("v")
        if parts.path.startswith(("/shorts/", "/embed/")):
            video = parts.path.split("/")[2]
    elif parts.hostname == "youtu.be":
        video = parts.path.strip("/")
    if video and regex.fullmatch(r"[\w-]{11}", video):
        return f"{invidious.rstrip('/') or 'https://www.youtube-nocookie.com'}/embed/{video}"
    return None


def text_html(text):
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"
