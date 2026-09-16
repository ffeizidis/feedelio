# Feedelio

A private, single-user, keyboard-first desktop RSS reader. The left pane combines your folder tree, subscriptions and folder-grouped article list; the right pane is for reading. No mobile layout or multi-user accounts.

## Run with Docker

```sh
docker compose up --build -d
```

Open **http://localhost:8000**. Import your Inoreader OPML under **Manage library → Data & connections**, or press **A** to add a feed. The library starts empty; no sample subscriptions are added.

Without the Compose plugin:

```sh
docker build -t feedelio:local .
docker run -d --name feedelio --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v feedelio-data:/data feedelio:local
```

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

`reader.sqlite` is source/cache storage. `app.sqlite` is authoritative for your reading state and processed articles. Full text and transcripts are indexed transactionally via FTS5 triggers. Restoring does not require the reader cache: the next refresh recreates it without duplicating restored articles.

## Feature coverage

All requested areas have an implementation. Integrations that depend on a publisher, a browser or a third-party service are identified below; this is not a promise that every site can be extracted or backfilled.

| Requested features | Implementation |
| --- | --- |
| 1–4: feed formats, conditional GET, deduplication | RSS 2.0, RSS 1.0, Atom and JSON Feed 1.1 through reader. ETag/Last-Modified caching. Normalized GUID, nonempty article URL and content hash deduplication; changed publisher content is updated while extracted content and reading state survive. |
| 5, 18–19: polling, intervals, health | Durable worker queue; manual refresh; per-feed manual interval or median recent posting-gap calculation, recalculated each fetch. Error backoff, last-check status and a 90-day quiet-feed indicator. A quiet feed is flagged, never automatically deleted. |
| 6–8, 14–17, 24–25: subscriptions and layout | Nested OPML import/export; validated strict folder tree with exactly one folder per feed; recursive unread counts; custom titles; site favicons; page and YouTube channel feed discovery; bulk move/tag/delete; two-pane desktop shell. |
| 9–13, 26–27: reading state | Read/unread and independent stars; mark feed/folder/all read; newest/oldest ordering; hide-read; system/manual dark mode; undo article changes and subscription deletion; recently-read ordering. Undo records expire after seven days during retention maintenance. |
| 20–22, 42: reading and search | SQLite FTS5 over titles, processed full text and transcripts; word counts and 230-wpm estimates; serif/sans/mono, size, width and line-height controls. Literal search terms are combined with AND. |
| 23: feed migration | Successful redirect chains made entirely of permanent 301/308 redirects migrate the subscription URL while preserving its identity and articles. Temporary redirects are not treated as permanent moves. |
| 28–30, 33–35: content processing | Queued Trafilatura extraction; custom CSS selectors; sanitization; small/hidden pixel removal; UTM and known click-ID stripping; custom UA, cookies and HTTP proxy; timed regex rewrite rules; canonical URL resolution and structured-data paywall detection. |
| 31–32: duplicates and rules | Near-identical recent cross-feed titles can be hidden; saved stories remain visible. Ordered match → read/star/tag/drop/rewrite rules, per feed or global; presets for Shorts URLs and detected paywall tags. Rules can be reapplied to stored articles. |
| 36–40: video, podcasts and supplied transcripts | Channel discovery and RSS subscriptions, click-to-load youtube-nocookie or configured Invidious player, audio enclosure playback, durable download/retry queue and server-local playback. Podcast transcript links, page transcript selectors/caption tracks, and caption URLs supplied by YouTube's page are supported. No ASR. |
| 41, 46: retention and archives | Retention purges old **read, unstarred** items; unread/starred items and archive-enabled feeds are protected. Tombstones prevent purged GUIDs from returning. Backfill accepts an archive feed and follows JSON `next_url` or Atom `rel=next`, up to 10 pages by default (100 maximum via core API). |
| 43: full JSON backup | One restorable JSON file includes subscriptions, folders, articles, transcripts, tags, states, settings, rules, history, undo, jobs, tombstones and base64-encoded downloaded episodes. Restore requires an empty library. |
| 44–45: habits and data saver | Per-feed read/saved totals, daily open events and reading-time estimates. Images/favicons disabled by default; per-article image loading; audio does not preload and video loads only on click. |
| 47–49: adding new sources | Companion extension toolbar opens subscription discovery for the current page. RSSHub instance/routes and newsletter-bridge URLs are ordinary subscriptions. CSS story-card and link selectors create a local synthetic stream from a page without a feed. |
| 50–51: notes and agents | Article + transcript export as Obsidian-compatible Markdown with YAML frontmatter. Stdio MCP exposes indexed search, article retrieval, subscription listing/creation and read/save changes. |
| 52: Chrome Reading List | Companion extension imports Reading List entries and syncs read state in both directions every minute while Chrome is running. Latest timestamp wins; the extension rechecks Chrome timestamps before applying server changes. Deletions do not propagate. |

### Integration boundaries

- Custom UA/cookies/proxies use request settings you supply. There is no CAPTCHA-solving service, browser-based challenge solver or guarantee of passing bot protection.
- Paywall detection uses `isAccessibleForFree: false` in source-page JSON-LD after extraction. Feed-text rules can catch other signals. Canonical resolution does not unlock subscription-only content. Enable automatic extraction on feeds where these tags should be detected automatically.
- YouTube feed items usually link to `/watch`; Shorts filtering catches items whose URL identifies `/shorts/`. The feed does not reliably label every Short, so the preset cannot classify all short videos.
- Captions must actually be provided and accessible. YouTube page formats and third-party Invidious/RSSHub availability can change. There is no audio-to-text generation or guaranteed transcript availability.
- Backfill can only retrieve history the publisher exposes. It cannot invent an archive beyond available feeds or pagination. Scrapers operate on server-rendered HTML; JavaScript-only sites need an external feed bridge.
- Offline episodes are offline **on the server**; the browser still needs a connection to Feedelio. JSON backups containing audio can be large and are assembled in memory; for large libraries, also take a stopped-container volume snapshot. Default JSON import limit: 2 GiB, configurable with `FEEDELIO_MAX_IMPORT_MB`.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| J / K or ↓ / ↑ | Next / previous article |
| Space | Page down in the reading pane |
| S | Save / unsave |
| M | Read / unread |
| U | Undo last change |
| / | Search |
| O | Open original |
| R | Queue refresh |
| A | Add subscription |
| ? | Shortcut guide |

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

For a remotely reachable instance, set a strong `FEEDELIO_TOKEN`, use HTTPS through your reverse proxy/load balancer, and restrict the instance's inbound network access to your own access path. The local Compose mapping intentionally binds port 8000 to loopback. An SSH tunnel also works without exposing the app port:

```sh
ssh -L 8000:127.0.0.1:8000 your-host
```

The single access token provides an HTTP-only, SameSite session cookie or Bearer authentication for the extension. There is no account or password database. Cross-origin mutations are rejected. Change the token to revoke sessions. Source HTML is sanitized on both server and client. Private/link-local network fetch targets are rejected by default, including redirects; opt in with `FEEDELIO_ALLOW_PRIVATE_NETWORK=1` only when needed for your feeds or proxy. For untrusted subscriptions on a cloud host, also use network-level egress restrictions against metadata/internal services.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `FEEDELIO_DATA` | `data` (`/data` in Docker) | Persistent library directory |
| `FEEDELIO_TOKEN` | empty | Single-user access token; required for remote exposure |
| `FEEDELIO_STATIC` | `web/dist` | Built frontend directory |
| `FEEDELIO_ALLOW_PRIVATE_NETWORK` | `0` | Allow private-network feeds/proxies when `1` |
| `FEEDELIO_MAX_EPISODE_MB` | `500` | Maximum bytes per downloaded episode, in MiB |
| `FEEDELIO_MAX_IMPORT_MB` | `2048` | Maximum declared request size, in MiB |

Backups contain your feed cookie/proxy settings as well as reading data. Keep exported files private. To upgrade: export a backup, rebuild the image, then recreate the container with the same volume. This first release has a versioned export format; future database schema changes require migrations.

## Verification

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
