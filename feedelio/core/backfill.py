"""Archive format adapters. Transport, scheduling and persistence live in Core.

Feed parsing remains reader's responsibility. Substack exposes a separate public
archive listing; it is not an RSS pagination endpoint or a paywall bypass.
"""

import io
import json
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from reader._parser.jsonfeed import JSONFeedParser

from .reader_extensions import ExtendedFeedparser


def initial(feed, url=None):
    explicit = url or feed["options"].get("backfill_url")
    source = explicit or feed["url"]
    substack = not explicit and (urlsplit(source).hostname or "").endswith(".substack.com")
    return dict(
        mode="substack" if substack else "feed",
        next_url=archive_url(source, 0) if substack else source,
        pages=0,
        articles=0,
        visited=[],
        pending=[],
        offset=0,
        attempts=0,
    )


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
