# Pane layout and paced archive ingestion

Verified 2026-09-17.

## Delivered

- Navigation stays on the left. Feed/folder streams and search results are on the right; opening an article replaces the list. Back to list preserves the mounted list and its scroll position. Escape returns to the list and clears search.
- Both list and article have bounded, independently scrollable containers and explicit scrollbar styling. The reader grid's missing minimum-height constraint was corrected.
- Pointer-draggable divider with a visible handle, keyboard Left/Right/Home/End controls, bounded widths, and persisted preference. Navigation remains usable at narrow desktop sidebar widths.
- Persisted titles-only or title-and-preview display.
- One durable archive job per active feed, seeded for existing subscriptions after a successful refresh. Progress/cursors survive worker restart and full JSON backup/restore.
- One archive page or article operation per minute across the worker; no sleeping worker loop and no ten/hundred-page cutoff. Other jobs can run during the delay. Redirects may require extra HTTP requests within an operation.
- Reader parsers handle RSS/Atom/JSON Feed pages. JSON next_url, RSS/Atom next and prev-archive links are followed. A small adapter supports public Substack archive listings and individually extracts missing articles with the existing extractor.
- Deduplication, pause/resume, repeated-page detection, exponential retries, Retry-After, and a five-attempt retry limit. Removed article pages (404/410) retain tagged archive previews and do not strand the rest of the archive. Access-denied errors stop with a visible error rather than attempting to bypass restrictions.

## Evidence

- `pytest tests -q`: **100 passed**, four existing dependency warnings.
- `pytest audit -q`: **45 passed** in 157.67 seconds, including real pointer resizing, actual wheel scrolling, scrollbar gutters, preference persistence and archive controls.
- Headless Chromium's default scrollbar-hiding flag is disabled in the audit so screenshots and checks reflect visible desktop scrollbars.
- Existing browser checks were adapted to click Back to list before list controls, reopen search results before exporting, and distinguish normal jobs from intentionally delayed archive jobs. Explicit archive tests still wait for archive completion. Navigation checks reflect starting from the list after changing filters.
- Build, Ruff and `git diff --check` passed.
- Docker image `feedelio:panes-archive` (`8cb2e80d273a`) built and smoke-tested with a temporary data directory, no external network, authenticated API, independent worker and frontend. The smoke container was automatically removed.
- Read-only inspection identified the existing subscription as Sasha Chapin's Substack with 20 stored articles. The new adapter discovered 20 older posts at archive offset 20. A separate isolated probe retrieved and stored one older public article, extracting 1,654 words. Neither probe wrote to the user's library.

## Boundaries

Only publisher-exposed history is recoverable. Generic feeds without pagination report this limitation and accept an explicit archive feed URL. Substack custom domains are not automatically identified. Paywalls and bot challenges are not bypassed; accessible bodies or clearly tagged previews are stored. The full live archive was not crawled during testing.

The running user container and data volume were not replaced. Deploy the new image with the existing volume and a configured access token to activate the changes and start automatic backfill.
