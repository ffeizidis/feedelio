# Feedelio

A private, single-user RSS reader replacing Inoreader. Desktop web only, no multi-user, no
mobile layout. Runs locally or on AWS from one Docker image.

`docs/architecture.md` is the decisions record and is binding — read it before adding
anything. This file is the working summary.

## Non-negotiables

- **[lemon24/reader](https://reader.readthedocs.io) does the work.** Feed parsing, conditional
  GET, dedup, read/important flags, tags, FTS5 search, per-feed scheduling, OPML — all built in.
  Roughly a third of the backlog is wiring and testing, not building (those issues carry the
  `covered-by-reader` label). Extend reader through its plugin hooks and tags before adding
  tables or post-processing. If you are writing a parser, stop.
- **Mature libraries over custom code. The less code the better.**
- **Only `feedelio.core` touches `reader`.** The API is thin: routes take `CoreDep` and run every
  blocking call through `await offload(...)`, because reader is synchronous. The worker is its
  own process and shares only the SQLite file.
- **UX is fixed:** two panes, not three — a merged sidebar on the left (article list grouped
  under collapsible folder headings) and the article on the right. Folders are a strict tree,
  one folder per feed. Keyboard-first.

## Layout

| Path | What lives there |
|---|---|
| `src/feedelio/core/` | Domain layer over reader — the contract everything else uses |
| `src/feedelio/api/` | FastAPI JSON API + serving the built SPA |
| `src/feedelio/worker/` | Scheduled polling loop, its own process |
| `frontend/` | React + TypeScript + Vite SPA; all API calls go through `frontend/src/api/client.ts` |
| `e2e/` | Playwright against the real stack (`stack.sh` builds the SPA, seeds SQLite, serves both) |
| `tests/` | Backend tests; `conftest.py` gives you `settings`, `core`, `loaded_core` |
| `.claude/skills/` | Agent skills, checked in. `.gitignore` ignores `.claude/worktrees/` only — do not re-broaden it |

Configuration is environment-only (`FEEDELIO_*`, see `src/feedelio/config.py`). No config file,
no admin UI.

## Commands

```bash
uv sync --all-groups
uv run pytest                                   # coverage gate: 90%
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run uvicorn feedelio.api.app:create_app --factory --reload
uv run python -m feedelio.worker

cd frontend && npm run lint && npm run typecheck && npm test && npm run build
cd e2e && npx playwright test                   # boots the real stack on :8791

.claude/skills/browser-check/run.sh .claude/skills/browser-check/scenarios/*.spec.ts
                                                # the kept library: does it still work *now*?

docker compose up --build                       # http://localhost:8000
```

`uv run pre-commit install` once; the hooks run ruff, mypy, eslint and tsc.

## How work lands

One issue, one branch, one PR. `main` is protected: PRs required, six required checks, linear
history, no force-push, branches auto-deleted on merge.

- Branch `feat/<issue>-<slug>`; TDD — the failing test comes first.
- Every gate above must pass locally before pushing.
- PR follows `.github/pull_request_template.md` with **real pasted output** as evidence, and
  `Closes #N`.
- Commit messages explain *why*, and end with
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Closing an issue means adding to the browser-check library**: a scenario in
  `.claude/skills/browser-check/scenarios/` that actually ran, plus its row in that skill's
  `COVERAGE.md` — or a line in the PR saying why the capability is not reachable from a
  running stack. `COVERAGE.md` is the answer to "what do we know works?"; it is only worth
  reading while that stays true.
- Merging is the human's call unless they say otherwise.

## Backlog

Seven milestones M0–M6, 61 issues numbered in dependency order, so the issue number is roughly
the build order. M0 (engineering setup) is done. M1 is sequenced MVP-first — the `mvp` label
marks the shortest path to dropping Inoreader; the milestone description carries the order.
Effort is in points, 1 pt ≈ half a focused day.

Tech tree of the whole backlog (dependencies + effort):
https://claude.ai/code/artifact/8f982c40-016c-4df3-8251-c897bdcc0f21

## Gotchas

- **`sgmllib3k` is a direct dependency on purpose.** reader's vendored feedparser imports the
  top-level `sgmllib` module, which feedparser 6.0.14 stopped pulling in; without the pin every
  fetch raises `ModuleNotFoundError`. Drop it when reader vendors a newer copy.
- **reader plugin names take the short form** (`.entry_dedupe`), not `reader.entry_dedupe` —
  the long form is deprecated. See `core/storage.py`.
- **No CodeQL and no build provenance.** Both need features GitHub does not offer user-owned
  private repos. `codeql.yml` and the attestation step are gated on `visibility == 'public'`
  rather than deleted; `security.yml` (pip-audit, npm audit, bandit) covers the gap.
- **GHCR needs a lowercase image name** — the owner is `ZenFeedbacker`, so `publish.yml`
  lowercases it once and shares it.
- **The e2e suite rebuilds the SPA every run** unless `E2E_SKIP_BUILD=1`, so it can never assert
  against a stale bundle.
