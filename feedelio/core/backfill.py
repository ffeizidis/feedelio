"""Archive format adapters. Transport, scheduling and persistence live in Core.

Feed parsing remains reader's responsibility. Substack exposes a separate public
archive listing; it is not an RSS pagination endpoint or a paywall bypass.
"""

import io
import json
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import regex
from bs4 import BeautifulSoup
from reader._parser.jsonfeed import JSONFeedParser

from .reader_extensions import ExtendedFeedparser

YOUTUBE_BROWSE = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
YOUTUBE_CLIENT = dict(clientName="WEB", clientVersion="2.20260918.00.00", hl="en", gl="US")


def initial(feed, url=None):
    explicit = url or feed["options"].get("backfill_url")
    source = explicit or feed["url"]
    substack = not explicit and (urlsplit(source).hostname or "").endswith(".substack.com")
    channel = None if explicit else youtube_channel(source)
    if channel:
        mode, next_url = "youtube", f"https://www.youtube.com/channel/{channel}/videos"
    else:
        mode, next_url = ("substack", archive_url(source, 0)) if substack else ("feed", source)
    return dict(
        mode=mode,
        next_url=next_url,
        pages=0,
        articles=0,
        visited=[],
        pending=[],
        offset=0,
        attempts=0,
        continuation=None,
    )


def host(state, feed_url):
    """The site a backfill's next request goes to: pacing is per site, so one slow
    archive never holds up the others."""
    url = (state or {}).get("next_url") or ((state or {}).get("pending") or [{}])[0].get("url") or feed_url
    return (urlsplit(url).hostname or "").removeprefix("www.")


def youtube_channel(url):
    """The channel id of a YouTube uploads feed, which lists only the newest 15 videos."""
    parts = urlsplit(url)
    if (parts.hostname or "").removeprefix("www.") == "youtube.com" and parts.path == "/feeds/videos.xml":
        return dict(parse_qsl(parts.query)).get("channel_id")
    return None


def youtube_request(continuation):
    """The browse call that continues a channel's video grid; the first page is plain HTML."""
    if not continuation:
        return None
    return dict(context=dict(client=YOUTUBE_CLIENT), continuation=continuation)


def youtube_listing(data):
    """Videos on a channel page or browse continuation, newest first, and the next token."""
    if data.lstrip().startswith(b"{"):
        actions = json.loads(data).get("onResponseReceivedActions", [])
        items = [
            i
            for a in actions
            for i in a.get("appendContinuationItemsAction", {}).get("continuationItems", [])
        ]
    else:
        found = regex.search(rb"ytInitialData\s*=\s*(\{.*?\});</script>", data, regex.S)
        if not found:
            raise ValueError("YouTube did not return the channel's video list.")
        items = next(_find(json.loads(found.group(1)), "richGridRenderer"), {}).get("contents", [])
    pending, token = [], None
    for item in items:
        if "continuationItemRenderer" in item:
            token = item["continuationItemRenderer"]["continuationEndpoint"]["continuationCommand"]["token"]
            continue
        lockup = next(_find(item, "lockupViewModel"), None)
        renderer = next(_find(item, "videoRenderer"), None) or next(_find(item, "gridVideoRenderer"), None)
        if lockup and lockup.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
            id = lockup.get("contentId")
            title = lockup["metadata"]["lockupMetadataViewModel"]["title"].get("content", "")
        elif renderer:
            id = renderer.get("videoId")
            title = "".join(r.get("text", "") for r in renderer.get("title", {}).get("runs", []))
        else:
            continue
        if id:
            pending.append(
                dict(
                    guid=f"yt:video:{id}",
                    url=f"https://www.youtube.com/watch?v={id}",
                    title=title,
                    published=None,
                    summary="",
                    tags=[],
                )
            )
    return pending, token


def _find(o, key):
    if isinstance(o, dict):
        if key in o:
            yield o[key]
        for v in o.values():
            yield from _find(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from _find(v, key)


def archive_url(source, offset):
    return (
        urljoin(source, "/api/v1/archive")
        + "?"
        + urlencode(dict(sort="new", search="", offset=offset, limit=20))
    )


def feed_page(data, url, headers):
    if data.lstrip().startswith(b"{"):
        parser = JSONFeedParser()
        next_url = json.loads(data).get("next_url")
    else:
        parser = ExtendedFeedparser()
        soup = BeautifulSoup(data, "xml")
        # RFC 5005 archives use prev-archive, paginated feeds use next.
        link = soup.find("link", rel="next") or soup.find("link", rel="prev-archive")
        next_url = link.get("href") if link else None
    _, entries = parser(url, io.BytesIO(data), headers)
    entries = list(entries)
    if not next_url and entries and isinstance(parser, ExtendedFeedparser):
        next_url = wordpress_page(soup, url)
    return entries, urljoin(url, next_url) if next_url else None


def wordpress_page(soup, url):
    """WordPress paginates feeds with ?paged=N but never advertises it; the last page is a 404."""
    generator = soup.find("generator")
    if not generator or "wordpress" not in generator.get_text().lower():
        return None
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    try:
        query["paged"] = str(int(query.get("paged", "1")) + 1)
    except ValueError:
        return None
    return urlunsplit(parts._replace(query=urlencode(query)))


def substack_page(data, url, offset):
    posts = json.loads(data)
    if not isinstance(posts, list):
        raise ValueError("The publisher returned an unexpected archive format.")
    pending = []
    for post in posts:
        if not isinstance(post, dict) or not post.get("canonical_url") or post.get("id") is None:
            raise ValueError("The publisher returned an invalid archive item.")
        pending.append(
            dict(
                guid=str(post["id"]),
                url=urljoin(url, post["canonical_url"]),
                title=post.get("title") or "(Untitled)",
                published=post.get("post_date"),
                summary=post.get("description") or "",
                tags=["paywall"] if post.get("audience") in ("only_paid", "only founding") else [],
            )
        )
    return pending, archive_url(url, offset + len(posts)) if posts else None
