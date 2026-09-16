# Feedelio adversarial browser audit

Date: 17 September 2026 (Europe/Athens). Application under test: commit `47f1e19` on `main`.

## Verdict

**Not ready to call feature-complete.** The original regression suite passes, but adversarial browser testing reproduces defects in keyboard navigation, duplicate visibility, import behavior, tracking protection, scheduling, audio selection, reading history and local video embedding. Application code has deliberately not been changed during this audit.

Final browser run: **26 passed, 10 failed, 0 skipped** across 36 scenarios in 171.75 seconds. Original regression suite: **24 passed**, with three dependency/parser warnings. Ruff and `git diff --check` pass. Runtime: Python 3.12.3, Node 24.15.0, Chromium 153.0.8010.12. A compact, committed [result snapshot](results.json) records each scenario's outcome.

Passing a scenario establishes the behavior tested, not every possible behavior of the feature. Every requested feature has an entry in the coverage matrix; external-service and incomplete coverage are explicitly identified.

## Method and isolation

- Actual Playwright Chromium running the built React application at 1440 × 1000, with keyboard input, native dialogs, file uploads/downloads, media playback, DOM assertions and screenshots. Appearance was also exercised at 200% CSS zoom, not a mobile breakpoint.
- Each scenario has a new temporary library and an actual Uvicorn subprocess. Background scenarios start the actual worker as a **separate process**. Mutable local HTTP publishers serve RSS, Atom, RDF, JSON, HTML, captions and playable WAV audio; requests and conditional headers are recorded. A real forwarding HTTP proxy is used for proxy testing.
- Reading-state scenarios seed fixtures through `feedelio.core`; import, retrieval, extraction and management operations then run through the browser. API reads verify persisted state and worker completion. Retention is queued through the browser's API because there is no UI “run purge now” button. A due timestamp and old article dates are seeded instead of waiting months.
- MCP is tested through its real stdio transport, with changes verified in the browser.
- The real unpacked extension runs in a fresh persistent Chromium profile using the actual `chrome.readingList` API. Only default server addresses in a temporary copy are changed to the isolated test port, preventing installation-time sync with the user's running library. No Chrome APIs or application endpoints are mocked.
- The existing Docker instance and `feedelio-data` volume are not used by these tests. The container remained healthy. No AWS resources or external accounts were changed.
- No failures are marked `xfail`, skipped, or repaired in the application to make the audit green. Tests remain separate from the original default `tests/` suite.

## Reproduce

From the repository root, after installing the development dependencies and building the frontend:

```sh
uv sync --extra dev
npm ci --prefix web
npm run build --prefix web
uv run playwright install chromium
uv run pytest audit -q
```

The audit intentionally exits nonzero while the defects below remain. Run serially: scenarios write a shared result summary and temporarily opt into local-network fixture fetching. Do not point the audit at a real library. Use `uv run pytest tests -q` for the original regression suite.

Evidence is generated in ignored `test-results/adversarial/`: `results.json`, `environment.json`, and a directory per scenario containing `trace.zip`, `final.png`, `accessibility.txt`, `server.log`, `publisher-requests.json`, uncaught page errors and browser-console messages. The real-extension trace and options screenshot are also saved at the artifact root. These artifacts are local, not committed; rerunning a scenario replaces its evidence. Inspect a trace with:

```sh
uv run playwright show-trace test-results/adversarial/test_keyboard_follows_visible_folder_group_order/trace.zip
```

## Reproduced findings

Priority is triage guidance: P1 blocks a central requirement or privacy expectation; P2 is a functional defect; P3 needs a product-semantics decision. Test names below are executable with `pytest audit -k NAME`.

| ID | Priority | Reproduction and observed result | Implementation boundary |
| --- | --- | --- | --- |
| B1 | P1 | In two folders, render A-new, A-old, B-middle. Open A-new and press J: B-middle opens, skipping the next visible row. `test_keyboard_follows_visible_folder_group_order` | `web/src/main.jsx`, `navigate()` uses the globally sorted `items`, not the folder-grouped display order. |
| B2 | P1 | Enable unread-only, press J twice, then K. The previous article cannot be revisited; the pane stays on the second article. `test_unread_keyboard_can_go_back_after_opening` | `navigate()` cannot find the selected article after marking it read removes it from the filtered list. |
| B3 | P2 | Discover a valid HTML page containing no feed links. The page itself is offered as a feed, without a no-feed result. `test_discovery_does_not_offer_html_as_a_feed` | `Core.discover()` falls back to the final URL without establishing that it is a feed. |
| B4 | P1 | Open an article containing an image with `style="width:1px;height:1px"`, then enable images. The publisher receives the pixel request. `test_css_sized_tracking_pixel_is_removed` | `core/content.py: sanitize()` handles tiny HTML dimension attributes and hidden styles, but not CSS dimensions. Images remain blocked while data saver is enabled. |
| B5 | P1 | Import identical titles in two feeds; one is hidden as a duplicate. Delete the original subscription. No version remains visible, although the other subscription and article still exist. `test_duplicate_recovers_when_original_feed_is_deleted` | The persisted `duplicate` flag is not reconsidered when the visible original disappears. Undoing deletion or disabling duplicate filtering recovers visibility; this is not physical data loss. |
| B6 | P2 | Import OPML with a valid subscription followed by an invalid `file:` URL. The UI reports failure, but the valid subscription has already been added. `test_malformed_opml_is_atomic` | `Core.import_opml()` commits subscriptions while walking rather than validating/committing the complete import. The invalid local file is rejected, not read. An explicitly reported partial import would be another defensible design. |
| B7 | P2 | Fetch a feed whose automatic interval is two hours. Change its manual interval to 17 minutes. The option saves, but `next_check` remains two hours away. `test_changing_manual_interval_reschedules_existing_feed` | `Core.edit_feed()` saves options without rescheduling. A manual refresh applies the interval on the next fetch. |
| B8 | P2 | An article has two audio enclosures. Download only the first, then reopen. Both players use the first download URL. `test_multiple_audio_enclosures_keep_their_own_download` | The article UI selects the first completed article-level download for every enclosure; returned download metadata does not identify its source URL. |
| B9 | P3 | Mark an unopened stream read, then open History. All those never-opened articles appear in “Recently read.” `test_bulk_mark_read_does_not_claim_unopened_articles_as_reading_history` | Bulk state updates populate `read_at`; History uses that field rather than open events. This conflicts with an actual-reading-history interpretation; decide whether History should mean opened or merely marked read. |
| B10 | P2 | Save a local `http://` Invidious instance, open a video and press Play. The iframe URL changes but its content never loads. `test_configured_local_invidious_is_actually_loaded` | The UI accepts HTTP, while the API's CSP allows only `frame-src https:`. HTTPS instance wiring is separate from this local-HTTP failure. Do not broadly weaken CSP to fix it. |

## Coverage of all 52 requested features

“Pass” refers to the specified fixture-backed check. “Defect” includes a successful normal path and a failing adversarial case. “Partial” explicitly identifies behavior not established in this audit.

| # | Requested feature | Browser audit result |
| --- | --- | --- |
| 1 | RSS 2.0 / Atom / RSS 1.0 | Pass: actual worker fetches all three, renders articles; malformed feed produces health error. |
| 2 | JSON Feed 1.1 | Pass: actual JSON Feed 1.1 fetched, parsed and displayed alongside XML feeds. |
| 3 | Conditional GET | Pass: publisher sees both validators on re-fetch and returns 304; article count stays stable. |
| 4 | GUID / link / hash deduplication | Pass: repeated fetches, duplicate links with different GUIDs and multiple GUID-less/link-less items remain stable. |
| 5 | Scheduled polling | Pass: a due subscription updates in the browser without a refresh click; independent worker advances the schedule. |
| 6 | OPML import / export | Defect B6: nested import and exported file pass; late validation failure leaves partial changes. |
| 7 | Strict tree; one folder per feed | Pass: nested assignment, recursive stream scoping and rejection of a parent cycle. |
| 8 | Two panes / merged sidebar | Defects B1–B2: shell, grouped rows and pagination render; keyboard navigation does not follow the visible stream reliably. |
| 9 | Read / unread | Pass for open, manual toggle, saved independence and Chrome round trip; unread-only navigation has B2. |
| 10 | Dark mode | Pass: manual dark, persistence, and System following an emulated dark preference. |
| 11 | Star / save | Pass: keyboard toggle, persisted saved state, rule and MCP starring, restore. |
| 12 | Mark feed / folder / all read | Pass: all three scopes, recursive child inclusion, outside-feed preservation and undo. History side effect is B9. |
| 13 | Sorting / hide-read | Defects B1–B2: chronological toggle, filter and pagination reset pass; navigation across grouping/filter changes fails. |
| 14 | Unread counts | Pass: changes after opening, scope marking and undo; recursive folder membership checked. Not stress-tested at huge scale. |
| 15 | Custom feed titles | Pass: edit title through management UI and preserve custom titles across restore. |
| 16 | Favicons | Pass: no favicon request by default; enabling image loading causes a request to the fixture site icon. |
| 17 | Page feed discovery | Defect B3: valid alternate-feed link discovery works; a feed-less page is wrongly offered as a subscription. |
| 18 | Manual / calculated interval | Defect B7: median-frequency calculation and initial manual setting work; editing an existing schedule is deferred incorrectly. |
| 19 | Feed health | Pass: malformed-feed error and seeded 100-day inactivity produce error/quiet indicators. No real 90-day soak. |
| 20 | Stored article search | Pass: title/body/transcript queries, Unicode and hostile query syntax, clear/reset on a second page. |
| 21 | Word count / reading time | Pass: 480-word article reports three minutes and appears in the reading pane. |
| 22 | Typography | Pass: mono selection, 32px text, 45ch width, 2.2 spacing, persistence and 200% CSS zoom. No physical-display usability certification. |
| 23 | Permanent URL migration | Pass: discovered 301 source migrates to destination while retaining the subscription and article. |
| 24 | Bulk feed management | Pass: multi-feed move, tags, delete and undo through management UI. Duplicate visibility after delete has B5. |
| 25 | Nested folders | Pass: parent/child OPML, restore, recursive read scope and cycle rejection. |
| 26 | Undo | Pass: read toggle, stream marking, article deletion and subscription deletion. Not every possible interleaving with worker changes. |
| 27 | Recently read | Defect B9: opening articles populates history, but bulk marking also populates it without opening. |
| 28 | Readability-style extraction | Pass: real queued Trafilatura extraction without selectors, navigation/footer exclusion, and explicit selector extraction. |
| 29 | Tracking removal | Defect B4: UTM cleanup, unsafe HTML and explicit-size pixels handled; CSS-sized pixels leak when images are enabled. |
| 30 | UA / cookies / proxy | Pass: publisher captures custom UA and cookie; real HTTP proxy observes feed request. Partial for actual bot challenges—no bypass claim. |
| 31 | Cross-feed duplicate filter | Defect B5: identical titles are suppressed but no survivor is promoted after original-feed deletion. Broader similarity precision/recall not measured. |
| 32 | Rule engine | Pass: read/star/tag/drop/rewrite, paywall and Shorts presets, reapplication and invalid-regex errors. A pathological regex times out without wedging the worker/UI. |
| 33 | CSS extraction rules | Pass: per-feed body/transcript selector and synthetic-feed story/link selectors against real fixture HTML. |
| 34 | Body rewrite rules | Pass: configured regex replacement appears in extracted content and Markdown export; malformed/pathological patterns are handled. |
| 35 | Canonical / AMP / paywall | Pass for fixture AMP-like source canonical link, UTM normalization and JSON-LD paywall tag. Partial for production publishers/syndication chains; does not unlock paywalls. |
| 36 | YouTube feeds / player | Partial: video article and click-to-load embed URL exercised. Live channel discovery/subscription and successful YouTube playback were not established. Generic Atom parsing is tested separately. |
| 37 | Podcast enclosures | Defect B8: audio markup and actual single-track WAV playback work; multiple tracks can select the wrong offline file. |
| 38 | Invidious / nocookie | Defect B10; partial otherwise: correct nocookie/configured iframe URLs are verified, not live remote playback. Local HTTP content is blocked by CSP. |
| 39 | Offline podcast queue | Defect B8: real download, playback after publisher returns 503, and deletion pass for one enclosure. Multiple-enclosure mapping fails. Offline means connected to the Feedelio server. |
| 40 | Provided transcripts | Pass: podcast transcript link, HTML selector and caption-track fetch become readable/searchable. Partial: production YouTube caption formats not tested. No ASR. |
| 41 | Retention | Pass: queued purge removes old read/unstarred item and preserves unread/starred items. Dates are seeded; no months-long soak. |
| 42 | Real indexed full-text search | Pass: fetched body and supplied transcript searchable through UI and MCP; hostile input leaves database usable. Large-library index performance not measured. |
| 43 | Full JSON backup / restore | Pass: browser downloads backup including base64 audio; UI restore preserves nested folders, state, tags, transcripts and appearance; cyclic backup rolls back; nonempty restore disabled. Partial: audio-containing backup exported but media restoration/playback was not round-tripped. |
| 44 | Reading statistics | Partial: Activity UI and saved total verified; open events exercised. Bulk-read/history semantics have B9; habit accuracy and skip inference are not comprehensively measured. |
| 45 | Data saver | Pass: article images/favicons initially make no requests, images load after opt-in, audio has no preload, video initially has no iframe. Pixel filtering after opt-in has B4. |
| 46 | Forever archive / backfill | Pass: archive-enabled feed survives purge; disabling archive permits eligible purge; archive JSON feed backfills through UI. Partial: multi-page historical publisher archives and long-term storage growth not browser-tested. |
| 47 | Subscribe browser button | Partial: real extension installs and subscribe deep link prefills the add dialog. Physical toolbar click and custom-host permission prompt were not exercised in headless mode. |
| 48 | RSSHub / bridges | Pass for configurable instance/route feeding the real worker from a local HTTP publisher. Partial: public RSSHub and a real newsletter bridge were not contacted. |
| 49 | Feed-less site scraping | Pass: UI-configured story/link selectors create three articles from fixture HTML. Feed-less autodiscovery UX has B3; JavaScript-rendered sites are outside this scraper's scope. |
| 50 | Obsidian export | Pass: actual Markdown download includes source frontmatter, rewritten article and transcript. Partial: no Obsidian vault import or highlight workflow exercised. |
| 51 | MCP | Pass: real stdio initialization, indexed search and state mutation visible in browser. Partial: not every MCP tool/error mode. No public remote MCP endpoint claimed. |
| 52 | Chrome Reading List | Pass: real extension API adds an entry, imports to Feedelio, sends opened/read back to Chrome, and sends Chrome-unread back to Feedelio. Partial: browser restart, minute alarm, conflict races and deletion behavior not exhaustively browser-tested. |

Additional privacy checks passed: wrong-token login rejection, correct login, HTTP-only session cookie, hostile-origin mutation rejection, and script/event/iframe injection sanitization. These are targeted checks, not a security certification. Local publisher fixtures require the explicit private-network opt-in; default SSRF protection and cloud metadata access are not comprehensively penetration-tested here.

## Remaining release checks

Fix and rerun B1–B8 and B10 before treating this as a dependable daily-reader replacement; resolve B9's intended semantics. Then verify a real Inoreader export, production YouTube/Invidious/RSSHub/transcript sources, large-library and large-backup behavior, browser restarts and permission prompts, plus deployment/TLS/persistence on the intended AWS host. Do not infer these from fixture-backed passes.
