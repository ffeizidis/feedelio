# What we actually know works

The record of everything verified against a **running Feedelio stack** — a real server, a
real SQLite library, real HTTP — by the scenarios in `scenarios/`. Every row below was
proved by a scenario that ran and passed; nothing is here on the strength of a unit test or
of somebody's memory.

Reproduce the whole list:

```bash
.claude/skills/browser-check/run.sh .claude/skills/browser-check/scenarios/*.spec.ts
```

**Read the "Verified" column before quoting anything from here.**

- **Browser** — asserted on the rendered page in Chromium. Someone could see it.
- **API** — asserted over HTTP against the running server. The behaviour is real; there is
  no UI for it yet, and saying otherwise would be a lie by omission.

The two-pane UI does not exist yet (#17), so most of this is necessarily API. As it lands,
rows move from API to Browser and the scenarios move their assertions with them.

## Verified

| Capability | Verified | Scenario | Issue |
|---|---|---|---|
| Atom 1.0, RSS 2.0 and RSS 1.0 all subscribe and parse, with the title and format reader reports surviving into the listing | API | `feed-formats.spec.ts` | #10 |
| Every subscribed feed lists its articles, each with a title, a body and its feed's title | API | `feed-formats.spec.ts` | #10 |
| Subscribing twice is a 409 | API | `feed-formats.spec.ts` | #10 |
| A URL that cannot be fetched is a 400 that names the URL and no server path, and leaves no broken subscription behind | API | `feed-formats.spec.ts` | #10 |
| A subscription made through the API shows up in the browser after a reload | Browser | `subscribe-shows-up.spec.ts` | #10 |
| A folder holds the feeds put into it, and reads as one merged stream of their articles | API | `folders.spec.ts` | #16 |
| One folder per feed: moving a feed elsewhere removes the previous membership | API | `folders.spec.ts` | #16 |
| Renaming a folder carries its feeds and their merged stream, and the old name is gone | API | `folders.spec.ts` | #16 |
| Deleting a folder keeps its feeds subscribed and unfiles them | API | `folders.spec.ts` | #16 |
| The unfiled feeds are a real group with an empty name, selectable as `?folder=` | API | `folders.spec.ts` | #16 |
| An OPML subscription list uploads as multipart over HTTP and reports what landed | API | `opml-round-trip.spec.ts` | #15 |
| Categories become folders — including an empty one — and a feed lands in the *innermost* category it sits under | API | `opml-round-trip.spec.ts` | #15 |
| A feed with no category stays unfiled | API | `opml-round-trip.spec.ts` | #15 |
| Re-importing adds nothing and never re-files a feed the library already has | API | `opml-round-trip.spec.ts` | #15 |
| The export downloads as a file: `application/xml; charset=utf-8` and `Content-Disposition: attachment; filename="feedelio-subscriptions-<date>.opml"` | API | `opml-round-trip.spec.ts` | #15 |
| The exported file re-imports into the same tree | API | `opml-round-trip.spec.ts` | #15 |
| The built SPA is served, boots, and reaches the API (no "Cannot reach the API" banner) | Browser | `spa-loads.spec.ts` | #62 |
| The Feeds and Unread counts on screen are the ones the database reports | Browser | `spa-loads.spec.ts` | #62 |
| Any client-side route returns the SPA, so a deep link or a refresh will not 404 | Browser | `spa-loads.spec.ts` | #62 |

## Not verified here

Not "broken" — just not something this library has run. Naming them is the point: an
unbounded list of things that "probably work" is how a coverage list stops being worth
reading.

| Not covered | Why |
|---|---|
| Mark as read, star, search, delete feed, feed refresh on demand | No endpoint yet — see `src/feedelio/api/routes.py` |
| The two-pane sidebar, the reading pane, keyboard navigation | Not built (#17) |
| The worker's polling loop | `e2e/stack.sh` boots the API only; the worker is its own process |
| Conditional GET, dedup, retention | reader's own behaviour, covered by reader's test suite and by `tests/` |
| Anything over the network | Scenarios resolve feeds from `tests/fixtures` through `FEEDELIO_FEED_ROOT` on purpose |
| The upload size cap (413) and malformed-OPML rejection (400) | Covered in `tests/test_api.py`; nothing about them changes over real HTTP |

## Known, deliberately not asserted

- An unknown `/api/...` path returns the SPA with 200, not a 404: the catch-all
  `/{path:path}` in `src/feedelio/api/app.py` is registered after the router and matches it.
  Real, and arguably wrong, but fixing it is app behaviour and belongs in its own issue —
  so no scenario asserts either way rather than freezing it in place.

## Keeping this honest

- **Closing an issue means a row here.** Add or extend a scenario, run it, and add the row —
  or write down in the PR why the capability cannot be reached from a running stack.
- **A row without a passing scenario is worse than no row.** If a scenario is deleted or a
  capability regresses, the row goes too.
- **Nothing runs this library automatically.** It is on-demand by design (see `SKILL.md`), so
  a stale row is possible: re-run the whole set before trusting the list after a big change.
