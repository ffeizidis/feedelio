---
name: browser-check
description: Test Feedelio in a real browser against a real server — check whether a feature actually works right now, verify the UI, reproduce a bug in the running app, or confirm a change end to end. Boots the full stack (built SPA + FastAPI + seeded SQLite) on a free port, runs scenario specs you write, and returns a verdict with screenshots and traces. Use for "does X work?", "check the UI", "try it in the browser", "reproduce this bug", "verify my change end to end". Not for adding CI regression tests.
---

# Browser-check: does it actually work?

Turns a scenario in words — "subscribe to a feed and check it appears", "put two feeds
in a folder and read the merged stream", "does the app come up at all?" — into a real
browser run against a real server, and an honest verdict.

## Three tiers, and which one you are in

| Tier | Where | Who runs it | Rule |
|---|---|---|---|
| CI regression suite | `e2e/tests/` | CI, every PR | Belongs to CI. Never add to it, edit it, or run scenarios from it. |
| **The kept scenario library** | `scenarios/` | You, on demand | The record of what is known to work. Checked in, replay-safe, runs as one batch. Every scenario here is listed in `COVERAGE.md`. |
| One-off scenarios | your scratch dir | You, once | Genuinely throwaway: reproducing a bug, poking at a half-built feature. Never committed. |

`COVERAGE.md` is the written answer to "what do we actually know works?" — capability,
scenario, issue, and whether it was verified **in the browser** or only **through the API**.

**Closing an issue means adding to both.** Add or extend a scenario in `scenarios/`, run the
whole library, and add the row to `COVERAGE.md` — or say in the PR why the capability cannot
be reached from a running stack. A row whose scenario never ran is the one failure this
whole thing exists to prevent.

## Run it

```bash
.claude/skills/browser-check/run.sh path/to/scenario.spec.ts [more.spec.ts ...]
```

Nothing else. `run.sh` picks a free port, builds the SPA, seeds a fresh SQLite library,
starts uvicorn, waits for `/api/health`, runs your scenarios in headless Chromium, kills
the server, and deletes the run directory on success (keeping it on failure).

Options:

| Flag | Effect |
|---|---|
| `--skip-build` | Skip the SPA rebuild. Fast, but you may be testing a stale `frontend/dist`. Only use it when you have not touched `frontend/`. |
| `--headed` | Run Chromium headed. |
| `--keep` | Keep the run directory even on success (for the trace of a passing run). |
| `-- <args>` | Everything after `--` goes to `playwright test`, e.g. `-- -g "merged stream"`, `-- --repeat-each=5` for a flake hunt. |

The whole kept library, which passes today:

```bash
.claude/skills/browser-check/run.sh .claude/skills/browser-check/scenarios/*.spec.ts
```

Add `-- --repeat-each=2` to run the set twice against one stack — that is the check that the
library is still replay-safe.

## Write a scenario

A scenario is one Playwright spec file. Copy the closest one from
`.claude/skills/browser-check/scenarios/`, edit it, and run it. A scenario that records a
capability stays in `scenarios/` and gets a row in `COVERAGE.md`; a one-off goes in your own
temp/scratchpad dir and is deleted.

```ts
import { expect, test } from '@playwright/test'

test('what you claim is true', async ({ page, request }) => {
  // `request` drives the JSON API; `page` drives the browser. Both are wired to
  // the running stack via baseURL — always use relative paths, never a hostname
  // and never a port.
  await request.post('/api/feeds', { data: { url: 'sample.rss' } })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Feedelio' })).toBeVisible()
})
```

Rules that keep a verdict worth having:

- **Set state up through the API, from `tests/fixtures/`.** The stack sets
  `FEEDELIO_FEED_ROOT=tests/fixtures`, so `sample.atom`, `sample.rss` and `sample.rdf`
  are valid feed URLs. Never subscribe to a URL on the live network: it is slow and its
  verdict is a coin toss.
- **The seeded library is one Atom feed** (`sample.atom`, 2 unread entries). Anything
  more, the scenario creates for itself.
- **Scenarios in one run share one library** and run one at a time, in file-name order. A
  kept scenario must survive its own second pass and everything the others left behind:
  tolerate what may already be there (`expect([201, 409]).toContain(...)`, delete-then-create
  for a name you need free) and put back anything you moved. Prove it with `--repeat-each=2`.
- **Assert something.** `run.sh` refuses a spec with no `expect(`; a file that only
  navigates is not a check.

## What is browser-verifiable today

**The two-pane UI does not exist yet** (issue #17). `frontend/src/App.tsx` is a
placeholder that renders an `<h1>Feedelio</h1>` and a `<dl>` of Version / Feeds / Unread
from `/api/health`. So:

| Verify in the **browser** (`page`) | Verify through the **API** (`request`) |
|---|---|
| the SPA is served and boots | subscribe (`POST /api/feeds`) |
| the page title and the `Feedelio` heading | list feeds (`GET /api/feeds`) |
| Version / Feeds / Unread counts, read from the database | list articles, filtered by feed / folder / read / important (`GET /api/entries`) |
| the `role=alert` "Cannot reach the API" banner | folders: create, rename, delete (`/api/folders`) |
| client-side route fallback (any path returns the SPA) | move a feed between folders (`PUT /api/feeds/folder`) |
| that an API-driven change shows up after a reload | health counts (`GET /api/health`) |

`src/feedelio/api/routes.py` is the full API surface — read it before assuming an
endpoint exists. There is no mark-as-read, star, search, or delete-feed endpoint yet.

**Report this distinction.** "Folders work" when only the API was exercised must be said
as "folders work through the API; there is no UI for them yet". Do not imply you clicked
something you could not click. `COVERAGE.md` carries the same split per row, and is the
fastest way to see what has already been checked which way.

When the real UI lands, move the assertions from the `request` column to the `page`
column. The runner, the config and the setup pattern do not change.

## Reporting the result

- **PASS** only when `run.sh` exits 0 *and* the scenarios asserted the thing that was
  asked about. A green run of a scenario that checks something adjacent is not an answer.
- **A scenario with no assertions is not a pass.** The runner blocks this, but the same
  applies to a scenario whose assertions are all trivially true.
- **FAIL comes with evidence.** On failure the run directory survives and holds:
  - `run.log` — the whole run, including uvicorn's request log (every request the
    scenario made, with its status code);
  - `results/<test>/test-failed-1.png` — a screenshot at the moment of failure;
  - `results/<test>/trace.zip` — open with `cd e2e && npx playwright show-trace <path>`;
  - `results/<test>/error-context.md` — the page's accessibility snapshot.

  Paste the assertion diff and name the evidence files. Do not paraphrase a failure.
- **Could not run** is a third outcome, and an honest one. Say so rather than guessing.

## Failure modes

**"no Chromium in ~/.cache/ms-playwright"** — install it with `cd e2e && npx playwright
install chromium`. Do **not** add `--with-deps`: that installs system packages via sudo,
which is not available here and will fail. If Chromium then will not start because of a
missing shared library, report that browser scenarios could not run; do not fall back to
an API-only run and call it a browser check.

**Port conflicts** — never hardcode a port. 8791 is the CI e2e suite's, and 8099/8100 are
taken by unrelated processes on this box. `run.sh` asks the kernel for a free port each
run and the config sets `reuseExistingServer: false`, so a run can neither collide with
nor silently adopt somebody else's server. If the stack fails to start on the chosen
port, that is a real bug — read `run.log`.

**"stack failed to start" / webServer timeout** — `run.log` has stack.sh's output. Usual
causes: the SPA build failed (a TypeScript error in `frontend/`), or the Python
environment is stale (`uv sync --all-groups`). The build is the slow part; `--skip-build`
skips it once you know `frontend/dist` is current.

**Missing node modules** — `run.sh` runs `npm ci` in `e2e/` (and `frontend/`, unless
`--skip-build`) the first time. In a fresh git worktree they are always missing, because
`node_modules/` is gitignored.

**A dirty working tree after a run** — should not happen; report it if it does. The run
directory is under `$TMPDIR`, the SQLite library and the copy of the SPA live in it, and
scenario files are copied there. The only thing a run touches inside the repo is
`frontend/dist/` and `node_modules/`, both gitignored. Check with `git status`.

**`.claude/` used to be gitignored wholesale** — it is now narrowed to
`.claude/worktrees/` so this skill can be committed. If you add files here and they do
not show up in `git status`, check `git check-ignore -v <path>` before blaming git.

## How it fits together

| File | Role |
|---|---|
| `run.sh` | The entry point: preflight, free port, scratch run dir, invoke Playwright, report. |
| `playwright.config.ts` | Copied into the run dir. One worker, no parallelism, `reuseExistingServer: false`, screenshot + trace on failure. Boots `e2e/stack.sh` as its `webServer`. |
| `scenarios/*.spec.ts` | The kept library: what is known to work, and the templates to copy. |
| `COVERAGE.md` | The written list of what those scenarios prove, browser vs API, with the issue each came from. |

`e2e/stack.sh` is the **only** stack booter — it builds the SPA, copies it to a static
dir, seeds SQLite with `sample.atom`, and execs uvicorn. It honours `E2E_PORT`,
`E2E_WORKDIR` and `E2E_SKIP_BUILD`, which is all `run.sh` needs. Do not write a second
one.
