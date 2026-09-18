# Feedelio redesign — implementation and verification

Implemented from `Personal RSS reader UI redesign.zip`, specifically the handoff README and chosen screens 3a and 4a/4c–4i. The 3b all-row hue-bar variant was not used. The prototype's scripts and inline styling were not copied into production.

## Delivered

- Three persistent desktop panes, default widths 252px / 590px / remaining space. Two invisible-at-rest 9px resize handles support pointer and keyboard operation and persist widths. The settings sidebar also resizes.
- One shared ground, blue accent, dark and off-white light themes; locally bundled Italianno wordmark and optional Newsreader body font. Font licenses ship in `web/public/fonts/`.
- Stable subscription-order feed hues stored in feed options, including migration of existing subscriptions. Renames, deletion and undo do not reshuffle hues.
- Titles-only 38px, snippets, and compact 28px lists. Independent read/star/feed controls; row space selects, titles open. Reader titles link to originals. Dates remain unwrapped while long titles elide.
- Full-screen library settings: Feeds with issue explanations and bulk actions; strict-tree Folders with drag/drop and keyboard-accessible destination selection; Appearance; Keyboard; Import & export. Existing Rules, Downloads, Activity, extraction, archive controls and integrations remain accessible.
- Search takes over the list pane, highlights matching tokens, and supports unread and indexed title-only filtering. Scoped mark-all uses the same title search condition.
- Add-feed discovery and subscription selection; nested new/edit-folder sheets; OPML file chooser and drop target; OPML, starred JSON and full-backup exports; confirmed, undoable Remove all.
- New shortcut map, including `u` unread-only, `z` undo, `g a` / `g s`, `n/p`, list-width brackets, list modes 1/2/3, Enter / Shift+Enter, comma for settings, and `?` toggle. Mark-all confirms above 50 unread items.
- No authentication changes: the existing local no-login configuration remains enabled, published only on 127.0.0.1.

The API remains thin. Reader access and new persistence/search/export operations remain inside `feedelio.core`; the worker is still a separate process.

## Verification

- Latest full run: `.venv/bin/pytest tests audit -q` — **163 passed**, four pre-existing warnings, 230.45 seconds.
- The ten redesign browser cases passed against final built assets. The updated pane assertions were also rerun separately: `.venv/bin/pytest audit/test_panes_backfill.py -q` — **6 passed**, 16.87 seconds.
- Frontend production build, Docker image build, Ruff, and `git diff --check` passed.
- Real Chromium coverage includes geometry at 1240, 1440, 1599, 1600, 1900 and 2560px across the regression and live checks; actual wheel scrolling; both resize boundaries; preference persistence; separate click targets; themes; folder drag/drop; nested sheets; indexed search; confirmations; original-page popups; OPML dropping; real JSON download; bulk undo; and 200% zoom.
- All 14 feed-label colours were measured in Chromium against both theme grounds and passed 4.5:1 contrast. Loaded font URLs are same-origin.
- Existing adversarial tests still cover sanitization, tracking/image privacy, extraction/transcripts, media/downloads, archive progress/pause/resume, backup/restore, Chrome integration and MCP. Old selectors and assertions were updated for intentional design changes, rather than keeping obsolete controls hidden in the UI.
- Native modal dialogs provide focus containment and Escape dismissal for full-screen library settings and sheets. Tests exercise nested folder sheets and the keyboard sheet toggle.

Artifacts (ignored): `test-results/redesign/` has screenshots of the design screens and the full-run browser results; `test-results/adversarial/` has traces and console snapshots; `test-results/live-redesign/` has live screenshots and geometry results.

## Live deployment

- Image: `feedelio:redesign-polish`, ID prefix `eaab43b7ab88`; active container `feedelio`, healthy.
- Existing volume: `feedelio-data:/data`. Local binding: `127.0.0.1:8000:8000`.
- A stopped-container backup was taken before upgrade; the old container is retained, stopped, for rollback. Private paths and details are in ignored `data/deployment-20260918.md`.
- Before/after SQLite quick checks returned `ok`. The sorted `(article id, read, starred)` fingerprints matched exactly: **278 articles, 44 read, 0 starred**.
- `.venv/bin/python audit/check_live_layout.py` passed on the deployed app, in a fresh browser with no Feedelio cookies. Six viewport widths, both resizers, title/snippet modes, article scrolling, source/folder lists and reload passed; no JavaScript errors. Temporary display preferences were restored. Opening one already-read article recorded a normal history visit without consuming an unread item.
- The final live screenshot was visually inspected: library, list, and reader remain horizontally aligned; the article and list each have their own scrollbar.

## Deliberate boundaries

- The app remains desktop-only, following the original product requirement. At 200% zoom the desktop canvas can scroll horizontally; no new mobile layout was introduced.
- Add feed discovers from site/feed/YouTube URLs, not a third-party name directory. The hint says so. Discovery results show real titles and URLs, not invented article counts or freshness.
- Existing saved appearance preferences are preserved. The supplied design defaults apply where no preference has been saved.
- Source samples are not imported. Older advanced workflows use the new shared tokens but retain their existing functional forms. Archive progress moved into each feed's settings to keep the reading list quiet.

## Typography review

The web-typography skill informed local font loading, fallback stacks, minimum chrome sizes, bounded reading measure, and zoom checks. Its diagnostic score for the tested default desktop configuration is **10/10**: 17px body, 1.7 line height, at most 75ch measure, distinct hierarchy, actual-browser checks, under 200KB of WOFF2 fonts (about 143KB across all included subsets), fallback stacks, usable 200% zoom, balanced headings, and distinct/underlined content links. This checklist score is not a comprehensive accessibility certification.

## Requested polish

- Single-feed lists omit repeated source labels and reclaim that grid column for titles. Folder and combined lists retain labels. Title, snippet, compact and scoped-search layouts are covered in Chromium.
- Left sidebar sizes increased: primary navigation 18px, feeds 17px, folders/search/manage 16px, counts 14px and section heading 13px. Row heights increased where needed. Typography diagnostic remains 10/10 for the tested default configuration, with no failing rows.
- The favicon now uses the actual Italianno lowercase-f outline, with its existing SIL OFL notice. It is self-contained SVG with a versioned link to refresh cached tab icons.
- The deployed app passed the live six-width check with no JavaScript errors. Test-only issues found during verification were corrected: feed scope is not restored on reload without an open article, and Playwright's wait callback must be a function under the production CSP.
