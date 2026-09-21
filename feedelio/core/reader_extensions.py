"""Small, version-pinned reader parser extension; no feed format reimplementation.

Keep this adapter covered when upgrading reader: its documented plugin interfaces
are internal. Feedparser still does all RSS/Atom parsing and normalization.
"""

import dataclasses
import io
from xml.sax import SAXParseException

from reader._parser.feedparser import FeedparserParser, _process_feed, feedparser

from .content import fingerprint, text_html


def duration_seconds(value):
    """Seconds from an itunes:duration ("1:02:03", "62:03" or "3723"); 0 when unparseable."""
    parts = str(value or "").strip().split(":")
    try:
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + float(part)
    except ValueError:
        return 0
    return int(seconds) if seconds > 0 else 0


def entry_fields(entry, durations=None):
    """One reader entry as ingest() keywords, so live feeds and archive pages
    read the same fields the same way."""
    body = next(
        (
            text_html(c.value) if c.type == "text/plain" else c.value
            for c in entry.content
            if c.type in ("text/html", "text/xhtml", "text/plain")
        ),
        entry.summary or "",
    )
    published = entry.published or entry.updated or getattr(entry, "added", None)
    return dict(
        guid=entry.id,
        title=entry.title or "",
        url=entry.link or "",
        body=body,
        author=", ".join(a.name or "" for a in entry.authors),
        published=published.isoformat() if published else None,
        enclosures=[dataclasses.asdict(e) for e in entry.enclosures],
        duration=(durations or {}).get(entry.id, 0),
    )


class ExtendedFeedparser(FeedparserParser):
    def __init__(self, durations=None):
        # reader's Entry has no room for itunes:duration; hand it back by entry id.
        self.durations = {} if durations is None else durations

    def __call__(self, url, resource, headers=None):
        result = feedparser.parse(
            resource,
            resolve_relative_uris=True,
            sanitize_html=True,
            response_headers={k.lower(): v for k, v in (headers or {}).items()},
        )
        # A stray entity or unclosed tag makes feedparser fall back to its loose
        # parser; keep what it recovered instead of failing the whole feed.
        if isinstance(result.get("bozo_exception"), SAXParseException) and result.version and result.entries:
            result["bozo"] = 0
        for entry in result.entries:
            if not entry.get("id") and not entry.get("link"):
                entry["id"] = fingerprint(entry.get("title", ""), entry.get("summary", ""))
            if entry.get("itunes_duration"):
                self.durations[entry.get("id") or entry.get("link")] = duration_seconds(
                    entry["itunes_duration"]
                )
            transcripts = entry.get("podcast_transcript", [])
            if isinstance(transcripts, dict):
                transcripts = [transcripts]
            for transcript in transcripts:
                href = transcript.get("url")
                if href:
                    entry.setdefault("links", []).append(
                        {"rel": "enclosure", "href": href, "type": transcript.get("type", "text/plain")}
                    )
        return _process_feed(url, result)


class BoundedParser:
    def __init__(self, parser, durations=None):
        self.parser = ExtendedFeedparser(durations) if isinstance(parser, FeedparserParser) else parser

    def __call__(self, url, resource, headers=None):
        data = resource.read(8_000_001)
        if len(data) > 8_000_000:
            raise ValueError("Feed exceeds the 8 MB size limit.")
        return self.parser(url, io.BytesIO(data), headers)
