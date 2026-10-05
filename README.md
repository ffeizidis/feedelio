# Feedelio

A private, single-user, keyboard-first desktop RSS reader. Three persistent panes hold folders/subscriptions, the article list, and the reader. Drag either boundary to resize. Only article titles open stories; read circles, stars, and feed labels have independent actions. Lists and article bodies scroll independently. No mobile layout or multi-user accounts.

## Run with Docker

```sh
docker compose up --build -d
```

Open **http://localhost:8000** — no login is required in the default local Docker configuration. Keep its port bound to localhost. Import your Inoreader OPML under **Manage library → Import & export**, or press **a** to add a feed. Feeds whose host no longer resolves are still imported and show a refresh error, so one dead blog never blocks a large export. After an import, refreshes take priority over the queued full-article extractions and archive pages, so new posts keep arriving while the backlog drains. A publisher that rate-limits a burst of extractions (HTTP 429) gets a later retry rather than a lost article. The library starts empty; no sample subscriptions are added.

Without the Compose plugin:

```sh
docker build -t feedelio:local .
docker run -d --name feedelio --restart unless-stopped \
  -e FEEDELIO_NO_AUTH=1 -p 127.0.0.1:8000:8000 -v feedelio-data:/data feedelio:local
```

After pulling changes, `deploy/redeploy.sh` rebuilds the image and replaces the container (via Compose when available), keeping the data volume.

One image runs **two independent processes**, supervised by Supervisor: Uvicorn for HTTP and `feedelio.worker` for polling, extraction, downloads, backfill, retention and rule jobs. Data lives in `/data`; keep this volume across upgrades. The worker holds an OS file lock, recovers interrupted jobs at startup and checks for work every ten seconds. Do not run multiple replicas against the same database.

## Run from source

Python **3.12** and Node **24** are the tested runtimes. Dependency versions are locked in `uv.lock` and `web/package-lock.json`.

```sh
uv sync --extra dev
npm ci --prefix web
npm run build --prefix web
uv run uvicorn feedelio.api:app --host 127.0.0.1 --port 8000
```

In a second terminal, from the same directory:

```sh
uv run feedelio-worker
```

For frontend development, run `npm run dev --prefix web`; Vite proxies `/api` to port 8000. Set the **same absolute `FEEDELIO_DATA` path** for the API, worker and MCP server. The default is `./data`.

## Architecture

```text
React desktop UI ── FastAPI ──┐
MCP stdio server ─────────────┼── feedelio.core ── reader.sqlite (reader-owned)
Independent worker ──────────┘                └─ app.sqlite + downloads/
```

Only `feedelio.core` imports or accesses `reader`, including its parser extensions. The API adapts HTTP to core operations; it never fetches subscriptions or runs background jobs in-process. SQLite WAL provides concurrent API/worker access without Redis or a database service.

The mature libraries do the specialized work: [reader](https://reader.readthedocs.io/en/stable/) for retrieval, RSS/Atom/JSON parsing and source persistence; SQLite FTS5 for indexed processed text; Trafilatura for extraction; Beautiful Soup for selectors; nh3 and DOMPurify for sanitization; RapidFuzz for title similarity; `regex` for time-limited rules; defusedxml for OPML; markdownify for Markdown; the official MCP SDK for stdio tools. React Query handles frontend server state.

`reader` is pinned to 3.26 because its documented internal plugin interfaces are used for bounded feed parsing, GUID-less fallback, provided podcast transcripts and raw-entry retention. Its bundled parser also needs the explicitly pinned `sgmllib3k` dependency. Upgrade this boundary with the integration tests.

Small HTTPcore/urllib3 adapters pin each direct TCP connection to validated DNS answers without changing HTTP Host, TLS SNI or certificate verification. Their connection hooks are version-pinned and covered by real TLS tests; keep those tests when upgrading. Ambient HTTP proxy environment variables are deliberately ignored. Explicit per-feed proxies require the private-network opt-in because the proxy controls destination resolution; only use a trusted proxy and enforce its egress policy separately.

`reader.sqlite` is source/cache storage. `app.sqlite` is authoritative for your reading state and processed articles. Full text and transcripts are indexed transactionally via FTS5 triggers. Restoring does not require the reader cache: the next refresh recreates it without duplicating restored articles.

## Feature coverage

All requested areas have an implementation. Integrations that depend on a publisher, a browser or a third-party service are identified below; this is not a promise that every site can be extracted or backfilled.

| Requested features | Implementation |
| --- | --- |
| 1–4: feed formats, conditional GET, deduplication | RSS 2.0, RSS 1.0, Atom and JSON Feed 1.1 through reader. ETag/Last-Modified caching. Normalized GUID, nonempty article URL and content hash deduplication; changed publisher content is updated while extracted content and reading state survive. |
| 5, 18–19: polling, intervals, health | Durable worker queue; feeds refresh eight at a time, at most two per site; every feed is checked at least hourly (15–60 minutes, from half its posting gap over the last 30 days, jittered) unless given its own interval; Update now goes first, a named feed ahead of the whole library, longest-waiting feeds first; an offline network (waking from sleep) retries in a minute without counting as a failure, and Retry-After is honoured. Error backoff, last-check status and a 14-day stale-feed indicator in the UI (the core health summary retains its 90-day quiet threshold). A quiet feed is flagged, never automatically deleted. |
| 6–8, 14–17, 24–25: subscriptions and layout | Nested OPML import/export; validated strict folder tree with exactly one folder per feed and folder names unique among siblings, case-insensitively (OPML outlines reuse a same-named sibling); feeds and folders nest by drag and drop in the library's Folders tab, which lists subfolders before feeds and starts collapsed; every folder picker can create a folder inline; recursive unread counts; custom titles; site favicons; page and YouTube channel feed discovery (falling back to feed links in the page body, robots.txt allow-lists and conventional paths such as /feed or /feed.xml when a site advertises none); subscribing refuses a feed already in the library, treating http/https, a www prefix and a trailing slash as the same feed; feeds whose XML needs feedparser's loose parser are kept rather than failed; bulk move/tag/delete; three-pane resizable desktop shell. |
| 9–13, 26–27: reading state | Read/unread and independent stars; mark feed/folder/all read; newest/oldest ordering; hide-read; system/manual dark mode; undo article changes and subscription deletion; recently-read ordering. Undo records expire after seven days during retention maintenance. |
| 20–22, 42: reading and search | SQLite FTS5 over titles, processed full text and transcripts; word counts and 230-wpm estimates; Georgia/Newsreader/system (and legacy mono), size, width and line-height controls. Literal search terms are combined with AND and match word prefixes, so results narrow as you type. |
| 23: feed migration | Successful redirect chains made entirely of permanent 301/308 redirects migrate the subscription URL while preserving its identity and articles. Temporary redirects are not treated as permanent moves. |
| 28–30, 33–35: content processing | Queued Trafilatura extraction; custom CSS selectors; sanitization; small/hidden pixel removal; UTM and known click-ID stripping; custom UA, cookies and HTTP proxy; timed regex rewrite rules; canonical URL resolution and structured-data paywall detection. |
| 31–32: duplicates and rules | Near-identical recent cross-feed titles can be hidden; saved stories remain visible. Ordered match → read/star/tag/drop/rewrite rules, per feed or global; presets for Shorts URLs and detected paywall tags. Rules can be reapplied to stored articles. |
| 36–40: video, podcasts and supplied transcripts | Channel discovery and RSS subscriptions, youtube-nocookie or configured Invidious player, loaded with the article, audio enclosure playback, durable download/retry queue and server-local playback. Podcast transcript links, page transcript selectors/caption tracks, and caption URLs supplied by YouTube's page are supported. No ASR. |
| 41, 46: retention and archives | Retention is off by default (0 days); when set, it purges old **read, unstarred** items; unread/starred items and archive-enabled feeds are protected. Tombstones prevent purged GUIDs from returning. Durable backfill follows JSON `next_url`, RSS/Atom `rel=next` / `prev-archive`, WordPress `?paged=N` feeds, public Substack archives, and YouTube channel pages (the uploads feed itself stops at 15 videos), without a 10/100-page cap. New subscriptions start read (posts already in the feed and backfilled history), so only later posts count as unread; **Manage library → Library settings** turns this off. |
| 43: full JSON backup | One restorable JSON file includes subscriptions, folders, articles, transcripts, tags, states, settings, rules, history, undo, jobs, tombstones and base64-encoded downloaded episodes. Restore requires an empty library. |
| 44–45: habits and data saver | Per-feed read/saved totals, daily open events, reading-time estimates and the archive's size on disk. Images/favicons disabled by default; per-article image loading; audio does not preload and video loads only on click. |
| 47–49: adding new sources | Companion extension toolbar opens subscription discovery for the current page. RSSHub instance/routes and newsletter-bridge URLs are ordinary subscriptions. CSS story-card and link selectors create a local synthetic stream from a page without a feed. |
| 50–51: notes and agents | Article + transcript export as Obsidian-compatible Markdown with YAML frontmatter. Stdio MCP exposes indexed search, article retrieval, subscription listing/creation and read/save changes. |
| 52: Chrome Reading List | Companion extension mirrors the Reading List into its own **Reading list** view (the book in the tray), listed under each page's website. Reading-list pages are not subscriptions: they stay out of the feed list, All articles and Bookmarked feeds, but can be saved and appear in History. Read state syncs in both directions every half minute while Chrome is running, and at once on a Chrome change; latest timestamp wins, and the extension rechecks Chrome timestamps before applying server changes. Removing a page from the Reading List removes it from Feedelio. |
| Videos and podcasts pages | A top-level folder can show on the **Videos** or **Podcasts** page (Manage library → Folders → Show on) instead of All articles; its subfolders and feeds go with it, and each page has its own place in the tray, unread count and address (`/videos`, `/podcasts`). Folders already named Videos or Podcasts move there on upgrade. Add feed looks at what a found feed carries (YouTube and other video links or video enclosures; audio enclosures) and offers only the folders on the matching page. |
| YouTube playlists | The companion extension reads the signed-in account's playlists, Watch Later included (Liked videos is left out), every half hour and sends only playlist and video ids and titles; the YouTube sign-in never leaves the browser. Each playlist is a feed in the **YouTube** view (the list icon in the tray), kept out of the feed list, All articles and rules. The worker keeps each video's title, channel, dates, duration, description, keywords, category, chapters, transcript (written captions before automatic ones) and thumbnail (`thumbs/`, included in backups), and looks at each again weekly. A video YouTube removes keeps everything and is marked unavailable; one that leaves its playlist is kept and marked. Stopping keeping a playlist in Feedelio is remembered by later syncs. |

### Integration boundaries

- Custom UA/cookies/proxies use request settings you supply. There is no CAPTCHA-solving service, browser-based challenge solver or guarantee of passing bot protection.
- Feed cookies are confined to the subscription's scheme/host/port. Cross-origin article/transcript/backfill requests do not receive them, and a permanent cross-origin feed migration clears the cookie. Reconfigure credentials explicitly for a new trusted origin.
- Paywall detection uses `isAccessibleForFree: false` in source-page JSON-LD after extraction. Feed-text rules can catch other signals. Canonical resolution does not unlock subscription-only content. Automatic extraction is on by default for every feed (**Manage library → Library settings**); a feed's own setting overrides it.
- YouTube feed items link to `/watch` for every video, and the watch page served to a feed reader is a cookie-consent interstitial, so video links are never page-extracted. Instead the extraction job asks YouTube whether `/shorts/<id>` exists (a HEAD request with consent declined): Shorts get the `short` tag and a `/shorts/` URL, which the **Skip YouTube Shorts** preset matches; the description stays as the article body, with paragraphs and links. The same request reads the video's length from the watch page, so a video article shows its duration where an article shows its reading time, and the player loads with the article, so one click plays. The article list marks videos, Shorts and paywalled items with small icons before the title.
- Captions must actually be provided and accessible. YouTube page formats and third-party Invidious/RSSHub availability can change. There is no audio-to-text generation or guaranteed transcript availability.
- Backfill starts automatically after the first successful refresh, including existing subscriptions on upgrade. It makes at most one archive-page/article operation per minute per site (so a library of many blogs backfills side by side while any one site is asked politely; YouTube, which serves one watch page per video, is asked every ten seconds), checkpoints after every step, and yields to other jobs between steps. HTTP redirects can require additional requests within an operation. Substack listings are fetched 20 at a time, then missing article pages individually; existing articles are skipped. YouTube channels are listed 30 videos at a time through the channel's video grid, then each missing video's watch page supplies its description, date and length. Progress, pause/resume and errors appear in the feed’s settings under Manage library → Feeds, and a one-line summary sits under the feed’s title above its article list. Transient failures use exponential backoff and honor `Retry-After`, stopping after five failed attempts until resumed. Backups include progress and pacing deadlines.
- Backfill can only retrieve history the publisher exposes. WordPress feeds are paged with `?paged=N` (detected from the feed's generator; the first missing page ends the archive). A feed without pagination reports that explicitly; configure **Archive feed URL** when available. Substack support uses its public archive endpoint on `*.substack.com`; custom domains without feed pagination need an explicit archive feed. It does not unlock paywalls, solve anti-bot challenges, or guarantee full text for every post; accessible body/preview text is stored. Scrapers operate on server-rendered HTML; JavaScript-only sites need an external feed bridge.
- Offline episodes are offline **on the server**; the browser still needs a connection to Feedelio. JSON backups containing audio can be large and are assembled in memory; for large libraries, also take a stopped-container volume snapshot. Default JSON import limit: 2 GiB, configurable with `FEEDELIO_MAX_IMPORT_MB`.

Every page has an address: `/f/<feed id>` for a feed's stream, `/d/<folder id>` for a folder, `/starred`, `/bookmarks` and `/history` for those views, `/a/<article id>` for an open article, `/library/<section>` (and `/library/feeds/<feed id>`, `/library/folders/<folder id>`) for the library; a folder stream's heading opens that folder's settings, as a feed's opens the feed's. Which folders are collapsed in the sidebar is remembered. A reload or a shared link lands on the same page, and an article opened by address shows within its feed's stream. The Downloads tab keeps the worker's log in one collapsed frame: the backlog per kind of job and the last thirty jobs with the time each last moved.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| J / K or ↓ / ↑ | Next / previous article |
| Space | Page down in the reading pane |
| S | Save / unsave |
| M | Read / unread |
| U | Unread-only toggle |
| Z | Undo last change |
| / | Search |
| O | Open original |
| R | Update now (this feed, or the whole library) |
| a / Shift+A | Add subscription / mark current stream read |
| Enter / Shift+Enter | Open selected article in reader / original page |
| N / P | Next / previous feed |
| g then a / g then s | All articles / Saved |
| [ / ] | Narrow / widen article list |
| 1 / 2 / 3 | Titles / snippets / compact list |
| , | Manage library |
| ? | Shortcut guide |
| Escape | Close search, sheet, or selected article |

The browser tab is named after what is open — the article, the library, or the stream. Article dates in the list are relative (now, 10m, 2h, 3d, 3w, 7mn, 2y; the exact date is the tooltip). When a feed is open, its title above the article list opens that feed's settings. The open reader, the library and a feed opened from the library's feed list each take a browser history entry, so Back — keyboard, toolbar or a mouse side button — closes the top one instead of leaving Feedelio. **Manage library** sits under History in the left pane, and **Bookmarked feeds** under Saved shows only articles from feeds you bookmarked (the bookmark button beside a feed's stream heading, or the checkbox in its settings); the eye button beside the feed-list heading hides caught-up feeds and folders (the feed being read always stays). In **Manage library → Feeds**, each feed's site is a link, and a feed's settings link to both its feed and its site; the Updated column is green when a feed is healthy, red when its last fetch failed and gold when it is stale or partial, and the status menu filters the list by that state. Focus either pane boundary and use Left/Right (20 px steps), Home or End to resize without a mouse. Widths and appearance preferences are saved in the library. **Manage library → Appearance** controls themes, accents, stable feed colours, counts/dots, wordmark, motion, list mode, reading fonts and density, and whether title clicks open the reader or original page. The default is dark, blue, 38px titles-only rows, and 17px Georgia. Existing preferences are preserved. Fonts are served locally, not from Google.

Shortcuts pause while typing or using a dialog. At 200% zoom the fixed desktop shell may scroll horizontally; it never changes into a mobile layout.

## Chrome companion

In Chrome 120+, open `chrome://extensions`, enable Developer mode and choose **Load unpacked → `extension/`**. Open the extension's options and set the Feedelio server URL and access token. Grant access to your chosen server when Chrome asks. The token stays in local extension storage, not Chrome Sync.

Chrome exposes Reading List through the extension-only [Reading List API](https://developer.chrome.com/docs/extensions/reference/api/readingList). It cannot be integrated directly from a regular web page. The extension requests `readingList`, `activeTab`, `alarms`, local storage and access to the configured server. An `!` badge indicates a synchronization error; details are in its options. Toolbar subscriptions open the normal Feedelio add-feed dialog.

## MCP

Example MCP client configuration (replace paths):

```json
{
  "mcpServers": {
    "feedelio": {
      "command": "/absolute/path/feedelio/.venv/bin/feedelio-mcp",
      "env": { "FEEDELIO_DATA": "/absolute/path/feedelio/data" }
    }
  }
}
```

For the Docker instance, use command `docker` with args `["exec", "-i", "feedelio", "feedelio-mcp"]`. The client inherits full access to this one library; stdio is not a public network endpoint.

## Private deployment, including AWS

The image is suitable for a **single Linux host**, including EC2, with a persistent local block-backed volume (for example EBS). Run one container with `/data` on that volume. SQLite WAL should not be placed on EFS/NFS, and this configuration is not intended for horizontally scaled ECS tasks.

For a remotely reachable instance, set `FEEDELIO_NO_AUTH=0` and a strong `FEEDELIO_TOKEN`, use HTTPS through your reverse proxy/load balancer, and restrict the instance's inbound network access to your own access path. The local Compose mapping intentionally binds port 8000 to loopback. An SSH tunnel also works without exposing the app port:

```sh
ssh -L 8000:127.0.0.1:8000 your-host
```

The single access token provides an HTTP-only, SameSite session cookie or Bearer authentication for the extension. There is no account or password database. Cross-origin mutations are rejected. Change the token to revoke sessions. Source HTML is sanitized on both server and client. Private/link-local network fetch targets are rejected by default, including redirects; opt in with `FEEDELIO_ALLOW_PRIVATE_NETWORK=1` only when needed for your feeds or proxy. For untrusted subscriptions on a cloud host, also use network-level egress restrictions against metadata/internal services.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `FEEDELIO_DATA` | `data` (`/data` in Docker) | Persistent library directory |
| `FEEDELIO_NO_AUTH` | `0` (`1` in Compose) | Disable login for localhost-published Docker; overrides any token |
| `FEEDELIO_TOKEN` | empty | Optional single-user access token; required for remotely exposed instances |
| `FEEDELIO_STATIC` | `web/dist` | Built frontend directory |
| `FEEDELIO_ALLOW_PRIVATE_NETWORK` | `0` | Allow private-network feeds/proxies when `1` |
| `FEEDELIO_MAX_EPISODE_MB` | `500` | Maximum bytes per downloaded episode, in MiB |
| `FEEDELIO_MAX_IMPORT_MB` | `2048` | Maximum declared request size, in MiB |

Backups contain your feed cookie/proxy settings as well as reading data. Keep exported files private. To upgrade: export a backup, rebuild the image, then recreate the container with the same volume. This first release has a versioned export format; future database schema changes require migrations.

Without explicit no-login mode, token-free access is limited to direct loopback clients using a loopback hostname. `FEEDELIO_NO_AUTH=1` also permits Docker bridge peers using a loopback hostname, without sessions or login. This mode relies on the localhost port binding, not the Host header, for network isolation: anyone who can reach the service can control the library. Never expose it publicly or through an unprotected reverse proxy. Cross-origin mutation and non-loopback Host checks remain enabled. Keep Uvicorn's forwarded-header trust restricted to your actual trusted proxy.

## Verification

The [pane and archive update](audit/PANES_ARCHIVE.md) records 100 regression tests, 45 browser scenarios, Docker validation and isolated live-source checks for the revised layout and paced backfill.

The [adversarial browser audit](audit/REPORT.md) records the ten reproduced defects, their verified fixes, and a 52-feature coverage matrix with explicit limitations. Run its separate real-browser suite with `uv run pytest audit -q`; all scenarios are expected to pass. Passing fixture-backed checks is not a feature-completeness certificate.

The subsequent [independent whole-codebase review](audit/REVIEW.md) records fourteen findings and their fixes, including credential isolation, DNS pinning, concurrent state preservation, transactional retention/restore and filtered-stream actions.

History records actual article opens, not bulk/manual mark-as-read actions. For local HTTP Invidious, configure a private IP address or `localhost` (and enable private-network access); CSP permits only that exact origin. Changing the instance reloads the page after closing settings, preserving the selected article. Public instances require HTTPS, and an HTTPS deployment may still block HTTP frames as mixed content.

```sh
uv sync --extra dev
npm ci --prefix web
npm run build --prefix web
uv run playwright install chromium
uv run pytest -q
uv run ruff check feedelio tests
```

Tests use isolated temporary libraries and local HTTP fixture servers: four feed formats, conditional requests, redirects, hash fallback, duplicate handling and publisher updates, strict folders, undo, search, extraction, supplied podcast transcripts, downloads, retention, OPML/JSON roundtrips, scraping/backfill, Chrome read-state conflicts, authentication and the core architecture boundary. Chromium exercises the actual built desktop UI, keyboard reading, search, themes and management dialogs. Browser screenshots are written to `test-results/`.

Live Chrome installation/permission granting and publisher-specific extraction, bot protection, Invidious/RSSHub routes, and AWS deployment need verification in your environment. Automated fixtures do not certify those external services.

Typography audit: **9/10** against the web-typography diagnostic. Article text defaults to 19px, 65ch, 1.7 line spacing, uses system fallbacks and no downloaded fonts, and was tested in Chromium including 200% zoom. Remaining check: review on the user's physical display; automated screenshots cannot establish physical-screen comfort.
