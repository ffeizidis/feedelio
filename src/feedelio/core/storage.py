"""Construction of the underlying :mod:`reader` instance.

Isolated from the service layer so that plugin and storage choices live in
exactly one place.
"""

from __future__ import annotations

from pathlib import Path

from reader import Reader, make_reader

#: reader plugins Feedelio always runs. Later milestones add their own hooks
#: through the same mechanism rather than by post-processing entries.
PLUGINS: tuple[str, ...] = (
    ".ua_fallback",  # retry 403s with a browser user agent
    ".entry_dedupe",  # near-duplicate entries across updates
    ".mark_as_read",  # regex mark-as-read rules (base for the rule engine)
)


def open_reader(db_path: Path, *, feed_root: str | None = None) -> Reader:
    """Open (creating if needed) the reader database at ``db_path``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    reader = make_reader(str(db_path), feed_root=feed_root, plugins=PLUGINS)
    reader.enable_search()
    return reader
