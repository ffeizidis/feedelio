# Architecture & UX decisions

The decisions every issue may assume. Change them here, in a PR, rather than
re-litigating them per feature. (Issue #1.)

## What Feedelio is

A private, single-user RSS reader replacing Inoreader. Desktop web only, no
multi-user support, no mobile layout. Runs locally or on AWS from one image.

## Core: the `reader` library

[lemon24/reader](https://reader.readthedocs.io) on SQLite is the domain and
storage layer. It already does the things a reader has to do — RSS 2.0 / Atom /
RSS 1.0 / JSON Feed parsing, conditional GET, GUID dedup, read/important flags,
`user_title`, feed and entry tags, FTS5 search, per-feed update scheduling,
OPML import/export, `Feed.last_exception` health, `change_feed_url()`. Roughly a
third of the backlog is wiring and testing, not building; those issues carry the
`covered-by-reader` label.

The one notable gap is entry deletion: `delete_entry()` is low-level only, so
retention and auto-purge (M5) are real work.

**Rule:** extend reader through its plugin hooks and tags before adding tables
or post-processing. Enabled plugins live in `feedelio/core/storage.py`.

## Layers

| Layer | Package | Rule |
|---|---|---|
| Core | `feedelio.core` | The only code that touches `reader`. Sync. |
| API | `feedelio.api` | FastAPI, thin. Every core call goes through `offload()` into a worker thread — reader is synchronous. Serves the built SPA. |
| Worker | `feedelio.worker` | Its own process. Asks reader which feeds are due (`update_feeds(scheduled=True)`) and fetches them. |
| SPA | `frontend/` | React + TypeScript + Vite. Talks to the API only through `src/api/client.ts`. |

The API and the worker share the SQLite file and never call each other.

## UX (fixed)

- **Two panes**, not three: a merged sidebar on the left — the article list
  grouped under collapsible folder headings — and the article on the right.
- **Folders are a strict tree, one folder per feed.** A feed lives in exactly
  one place; nesting is allowed, membership in two folders is not. They are a
  convention over reader's tags, not a table: `folder:<name>` on a feed is
  membership, the same key on the reader itself is existence (so an empty
  folder survives), and `/` is reserved for the nested-folder path. The service
  layer is the only writer of those keys, which is what enforces "exactly one".
- **OPML categories are folders**, in both directions (#15). Importing puts a
  feed in the *innermost* category it sits under — one folder per feed has to
  stay true, and that name is the leaf of the path nested folders (M2) will
  give it; a feed with no category stays unfiled, and every category becomes a
  folder even when it is empty. An import never re-files a feed the library
  already has, and never fetches: the worker collects the new feeds. Exporting
  writes one category outline per folder, with the unfiled feeds at the top
  level, so the file imports back into the same tree.
- Keyboard-first reading; the mouse is optional.

## Deploy

One multi-stage image: the SPA is built with Node, then copied into a Python
image that serves it alongside the API. `docker compose up` runs the API and
the worker off the same image with SQLite on a volume. Pushes to `main` publish
`ghcr.io/zenfeedbacker/feedelio:{latest,sha-…}`.

Configuration is environment-only (`FEEDELIO_*`, see `feedelio/config.py`).
There is no config file and no admin UI.

## Engineering

- Mature libraries over custom code; the less code the better.
- TDD: the test comes first, and CI enforces a 90% coverage floor on both
  stacks.
- Everything lands through a reviewed PR with green checks; `main` is protected.
- `uv` for Python, `npm` for JS, ruff + mypy (strict) and ESLint + Prettier +
  `tsc` as the gates. Playwright smoke-tests the real stack.

## Known constraints

- CodeQL needs GitHub Advanced Security, which this private repo does not have.
  `security.yml` runs pip-audit, `npm audit`, and bandit instead; `codeql.yml`
  is kept, disabled, for the day that changes.
