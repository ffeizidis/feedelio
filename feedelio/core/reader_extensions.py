"""Small, version-pinned reader parser extension; no feed format reimplementation.

Keep this adapter covered when upgrading reader: its documented plugin interfaces
are internal. Feedparser still does all RSS/Atom parsing and normalization.
"""

import io

from reader._parser.feedparser import FeedparserParser, _process_feed, feedparser

from .content import fingerprint


class ExtendedFeedparser(FeedparserParser):
    def __call__(self, url, resource, headers=None):
        result = feedparser.parse(
            resource,
            resolve_relative_uris=True,
            sanitize_html=True,
            response_headers={k.lower(): v for k, v in (headers or {}).items()},
        )
        for entry in result.entries:
            if not entry.get("id") and not entry.get("link"):
                entry["id"] = fingerprint(entry.get("title", ""), entry.get("summary", ""))
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
    def __init__(self, parser):
        self.parser = ExtendedFeedparser() if isinstance(parser, FeedparserParser) else parser

    def __call__(self, url, resource, headers=None):
        data = resource.read(8_000_001)
        if len(data) > 8_000_000:
            raise ValueError("Feed exceeds the 8 MB size limit.")
        return self.parser(url, io.BytesIO(data), headers)
