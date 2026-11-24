"""Content processing uses maintained parsers, sanitizers, extractors and matchers."""

import hashlib
import html
import ipaddress
import json
import os
import socket
import warnings
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import nh3
import regex
import tinycss2
import trafilatura
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

from .network import addresses, http_client

# A body that is just a URL (some video descriptions) is parsed on purpose.
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)


class UnresolvedHost(ValueError):
    """The host name does not resolve right now; fetching would fail, not leak."""


def safe_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an http(s) URL without embedded credentials.")
    if os.getenv("FEEDELIO_ALLOW_PRIVATE_NETWORK") != "1":
        try:
            addresses(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise UnresolvedHost(f"The host {parts.hostname} could not be resolved.") from exc
    return urlunsplit(parts)


def clean_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def same_feed(a: str, b: str) -> bool:
    """One feed served under variant spellings: scheme, a www prefix, a trailing slash, case."""

    def key(url):
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").removeprefix("www.")
        return host, parts.port, parts.path.rstrip("/") or "/", parts.query

    return key(a) == key(b)


def origin(url: str):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def request_options(feed):
    """Bind user-supplied credentials to the subscription, not publisher links."""
    return feed["options"] | {"cookie_origin": feed["url"]}


def local_frame_origin(url: str) -> str:
    """Return only an exact, CSP-safe local HTTP origin, never a wildcard."""
    parts = urlsplit(url)
    if parts.scheme != "http":
        return ""
    host = parts.hostname or ""
    try:
        local = ipaddress.ip_address(host).is_private
    except ValueError:
        local = host == "localhost"
    if not local or parts.username or parts.password:
        raise ValueError("Use HTTPS for Invidious, or a local HTTP IP address/localhost.")
    host = f"[{host}]" if ":" in host else host
    return f"http://{host}" + (f":{parts.port}" if parts.port else "")


USER_AGENT = "Feedelio/0.1 (+private feed reader)"


def request_headers(options=None, cookie=None):
    """Who Feedelio says it is, per feed: one place, so every request agrees."""
    options = options or {}
    headers = {"User-Agent": options.get("user_agent") or USER_AGENT}
    if cookie or options.get("cookie"):
        headers["Cookie"] = cookie or options["cookie"]
    return headers


def bounded(chunks, max_bytes, what="Response"):
    """Yield a streamed body, refusing to hold more than max_bytes of it."""
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"{what} exceeds the configured size limit.")
        yield chunk


def is_youtube(url):
    host = (urlsplit(url).hostname or "").removeprefix("www.").removeprefix("m.")
    return host in ("youtube.com", "youtu.be")


def fetch(url: str, options=None, max_bytes=8_000_000):
    options = options or {}
    headers = request_headers(options)
    credential_origin = origin(options.get("cookie_origin") or url)
    with http_client(options, timeout=30) as client:
        for _ in range(10):
            safe_url(url)
            if origin(url) != credential_origin:
                headers.pop("Cookie", None)
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                data = b"".join(bounded(response.iter_bytes(), max_bytes))
                return data, str(response.url), response.headers
    raise ValueError("Too many redirects.")


def sanitize(body: str, base: str) -> str:
    soup = BeautifulSoup(body or "", "html.parser")
    for tag in soup.select("script,style,iframe,object,embed,form,input,button,link,meta,svg"):
        tag.decompose()
    for img in soup.select("img"):
        styles = {}
        for declaration in tinycss2.parse_blocks_contents(
            img.get("style", ""), skip_comments=True, skip_whitespace=True
        ):
            if declaration.type == "declaration":
                styles[declaration.lower_name] = [
                    v for v in declaration.value if v.type not in ("whitespace", "comment")
                ]
        tiny = any(
            str(img.get(k, "")).removesuffix("px").isdigit() and int(str(img.get(k)).removesuffix("px")) <= 2
            for k in ("width", "height")
        )
        for key in ("width", "height", "max-width", "max-height"):
            values = styles.get(key, [])
            if len(values) == 1:
                value = values[0]
                tiny |= (
                    value.type == "dimension" and value.lower_unit == "px" and 0 <= value.value <= 2
                ) or (value.type == "number" and value.value == 0)
        hidden = any(
            len(styles.get(key, [])) == 1
            and styles[key][0].type == "ident"
            and styles[key][0].value.lower() == value
            for key, value in (("display", "none"), ("visibility", "hidden"))
        )
        if tiny or hidden:
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


def extract(url, options=None, *, document=None, transcripts=True):
    options = {"cookie_origin": url} | (options or {})
    data, document_url, _ = document if document is not None else fetch(url, options)
    soup = BeautifulSoup(data, "html.parser")
    canonical = soup.select_one('link[rel~="canonical"][href]')
    final = clean_url(urljoin(document_url, canonical["href"])) if canonical else clean_url(document_url)
    if urlsplit(final).scheme not in ("http", "https"):
        final = clean_url(document_url)
    selector = options.get("selector")
    if selector:
        selected = soup.select(selector)
        if not selected:
            raise ValueError("The configured article CSS selector matched no elements.")
        body = "".join(map(str, selected))
    else:
        body = _extract_body(data)
    try:
        transcript = provided_transcript(soup, data, document_url, options) if transcripts else ""
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        # Optional captions must not discard an otherwise usable article body.
        transcript = ""
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
        "body": sanitize(body, document_url),
        "url": final,
        "transcript": transcript,
        "paywall": paywall,
        "title": soup.title.get_text(strip=True) if soup.title else final,
    }


def _extract_body(data):
    """Prefer the extraction with links, unless keeping links cost article text.

    trafilatura (up to at least 2.4.0) drops prose that sits loose in a block next to an
    inline link when ``include_links`` is on — Blogger posts lose whole paragraphs and
    keep only the link text. Links are worth less than the words around them.
    """
    options = dict(output_format="html", include_images=True, include_tables=True)
    linked = trafilatura.extract(data, include_links=True, **options)
    unlinked = trafilatura.extract(data, include_links=False, **options)
    if unlinked and len(plain(linked)) < 0.95 * len(plain(unlinked)):
        return unlinked
    return linked


def provided_transcript(soup, data, document_url, options):
    transcript = ""
    if options.get("transcript_selector"):
        transcript = "\n".join(
            node.get_text(" ", strip=True) for node in soup.select(options["transcript_selector"])
        )
    track = soup.select_one(
        'track[kind="captions"][src], track[kind="subtitles"][src], a[rel="transcript"][href]'
    )
    if track and not transcript:
        raw, _, _ = fetch(urljoin(document_url, track.get("src") or track.get("href")), options)
        transcript = plain(raw.decode("utf-8", errors="replace"))
    elif embed_url(document_url) and not transcript:
        # Use caption tracks already supplied by the source player; never run ASR.
        document = data.decode("utf-8", errors="replace")
        match = regex.search(r'"captionTracks"\s*:\s*', document)
        if match:
            tracks, _ = json.JSONDecoder().raw_decode(document[match.end() :])
            if tracks and tracks[0].get("baseUrl"):
                raw, _, _ = fetch(tracks[0]["baseUrl"], options)
                transcript = plain(raw.decode("utf-8", errors="replace"))
    return transcript


def embed_url(url, invidious=""):
    parts = urlsplit(url)
    video = None
    if (parts.hostname or "").removeprefix("www.").removeprefix("m.") == "youtube.com":
        video = dict(parse_qsl(parts.query)).get("v")
        if parts.path.startswith(("/shorts/", "/embed/")):
            video = parts.path.split("/")[2]
    elif parts.hostname == "youtu.be":
        video = parts.path.strip("/")
    if video and regex.fullmatch(r"[\w-]{11}", video):
        return f"{invidious.rstrip('/') or 'https://www.youtube-nocookie.com'}/embed/{video}"
    return None


def text_html(text):
    """Plain text (a video description, a text/plain summary) as readable HTML."""
    paragraphs = []
    for block in regex.split(r"\n\s*\n", text.strip()):
        escaped = regex.sub(
            r"https?://[^\s<>\"']+", lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', html.escape(block)
        )
        paragraphs.append("<p>" + escaped.replace("\n", "<br>") + "</p>")
    return "".join(paragraphs)


def looks_like_html(body):
    return bool(regex.search(r"<[a-zA-Z][^>]*>", body or ""))


def video_id(url):
    embed = embed_url(url)
    return embed.rsplit("/", 1)[1] if embed else None


# Where watch pages come from; tests point it at a local server.
YOUTUBE = "https://www.youtube.com"


def probe_video(url, options=None):
    """Classify a YouTube link without loading the page.

    Watch pages served to a feed reader are consent interstitials, so a page
    extraction would replace the video with cookie boilerplate. YouTube answers
    /shorts/<id> with 200 for a Short and redirects to /watch for everything
    else; the SOCS cookie declines the consent prompt.
    """
    id = video_id(url)
    headers = request_headers(options, cookie="SOCS=CAI")
    video = dict(
        short=False,
        url=url,
        duration=0,
        title="",
        description="",
        author="",
        published=None,
        status="",
        upcoming=False,
    )
    with http_client(options, timeout=30) as client:
        response = client.head(f"{YOUTUBE}/shorts/{id}", headers=headers)
        if response.status_code == 200:
            video.update(short=True, url=f"https://www.youtube.com/shorts/{id}")
        page = client.get(f"{YOUTUBE}/watch?v={id}", headers=headers)
        if page.status_code == 429 or page.status_code >= 500:
            page.raise_for_status()  # Worth a retry later, unlike a private or removed video.
        if page.status_code == 200:
            video.update(watch_page_details(page.text[:4_000_000]))
        elif page.status_code in (404, 410):
            video["status"] = "ERROR"
    return video


def watch_page_details(text):
    """What the archive keeps of a video: the page's schema.org metadata and player description."""
    meta = {}  # The video's own name comes before the channel's.
    for m in regex.finditer(r'<meta itemprop="(\w+)" content="([^"]*)"', text):
        meta.setdefault(m.group(1), html.unescape(m.group(2)))
    strings = {}
    for key in ("shortDescription", "author"):
        found = regex.search(r'"' + key + r'":("(?:[^"\\]|\\.)*")', text)
        strings[key] = json.loads(found.group(1)) if found else ""
    player = embedded_json(text, r"ytInitialPlayerResponse\s*=\s*") or {}
    details = player.get("videoDetails") or {}
    micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    thumbnails = (details.get("thumbnail") or {}).get("thumbnails") or []
    tracks = ((player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}).get(
        "captionTracks"
    ) or []
    # Captions someone wrote come before YouTube's automatic ones.
    tracks = sorted((t for t in tracks if t.get("baseUrl")), key=lambda t: t.get("kind") == "asr")
    description = strings["shortDescription"] or str(details.get("shortDescription") or "")
    return dict(
        duration=iso_duration(meta.get("duration")),
        title=meta.get("name", ""),
        description=description,
        author=strings["author"] or str(details.get("author") or ""),
        published=utc(meta.get("datePublished")),
        thumbnail=max(thumbnails, key=lambda t: t.get("width") or 0)["url"] if thumbnails else "",
        keywords=[str(k) for k in details.get("keywords") or []],
        category=str(micro.get("category") or ""),
        channel_url=f"https://www.youtube.com/channel/{details['channelId']}"
        if regex.fullmatch(r"[\w-]+", str(details.get("channelId") or ""))
        else "",
        chapters=page_chapters(text) or description_chapters(description),
        captions=tracks[0]["baseUrl"] if tracks else "",
        api_key=(regex.search(r'"INNERTUBE_API_KEY":"([\w-]+)"', text) or [None, ""])[1],
        status=str((player.get("playabilityStatus") or {}).get("status") or ""),
        # A premiere or stream that has not started: "LIVE_STREAM_OFFLINE" until it does.
        upcoming=bool(details.get("isUpcoming"))
        or (player.get("playabilityStatus") or {}).get("status") == "LIVE_STREAM_OFFLINE",
    )


def video_captions(id, api_key, options=None):
    """A video's captions as text, written ones before automatic ones.

    The caption links on a watch page now need a token only YouTube's own
    player makes, and come back empty; the ones its Android app is given do not.
    """
    with http_client(options, timeout=30) as client:
        response = client.post(
            f"{YOUTUBE}/youtubei/v1/player?key={api_key}",
            json={
                "context": {"client": {"clientName": "ANDROID", "clientVersion": "20.10.38"}},
                "videoId": id,
            },
            headers=request_headers(options),
        )
        response.raise_for_status()
        player = response.json()
        tracks = ((player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}).get(
            "captionTracks"
        ) or []
        tracks = sorted((t for t in tracks if t.get("baseUrl")), key=lambda t: t.get("kind") == "asr")
        if not tracks:
            return ""
        url = tracks[0]["baseUrl"].replace("&fmt=srv3", "")
        safe_url(url)
        return caption_text(client.get(url, headers=request_headers(options)).text)


def caption_text(xml):
    """Timed-text XML as plain lines; its text is escaped twice."""
    lines = (
        html.unescape(html.unescape(regex.sub(r"<[^>]+>", "", m.group(1)))).strip()
        for m in regex.finditer(r"<text[^>]*>(.*?)</text>", xml, flags=regex.S)
    )
    return "\n".join(line for line in lines if line)


def embedded_json(text, prefix):
    """The JSON object a page assigns after the given pattern, or None."""
    found = regex.search(prefix, text)
    if not found:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[found.end() :])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def page_chapters(text):
    """The chapters YouTube shows under the player."""
    chapters = []
    for found in regex.finditer(r'"chapterRenderer"\s*:\s*', text):
        try:
            chapter, _ = json.JSONDecoder().raw_decode(text[found.end() :])
            title = chapter["title"].get("simpleText") or "".join(
                r.get("text", "") for r in chapter["title"].get("runs", [])
            )
            start = int(chapter["timeRangeStartMillis"]) // 1000
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if title and not any(c["start"] == start for c in chapters):
            chapters.append(dict(start=start, title=title))
    return sorted(chapters, key=lambda c: c["start"])


def description_chapters(description):
    """Chapters as uploaders write them: a timestamp per line, the first at 0:00."""
    chapters = []
    for line in (description or "").splitlines():
        found = regex.match(r"\s*[\[(]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\])]?\s*[-–—:|]?\s*(.+)", line)
        if found:
            parts = [int(n) for n in found.group(1).split(":")]
            start = sum(n * 60**i for i, n in enumerate(reversed(parts)))
            chapters.append(dict(start=start, title=found.group(2).strip()))
    # YouTube's own rule: three or more, starting at the beginning, in order.
    starts = [c["start"] for c in chapters]
    if len(chapters) < 3 or starts[0] != 0 or starts != sorted(starts):
        return []
    return chapters


def utc(stamp):
    """An ISO timestamp with any offset, stored the way the rest of the archive sorts: in UTC."""
    try:
        return datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def iso_duration(value):
    """PT1H2M3S → seconds; anything unexpected is 0 rather than an error."""
    found = regex.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not found:
        return 0
    days, hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds
