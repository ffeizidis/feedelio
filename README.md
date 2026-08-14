# Feedelio

A private, single-user RSS reader. Desktop web only, runs locally or on AWS from one
Docker image. Built on [lemon24/reader](https://github.com/lemon24/reader), which does the
feed fetching, parsing, dedup, tags, and search — Feedelio adds the API, the worker, and
the two-pane reading UI.

## Quick start

```bash
docker compose up --build          # http://localhost:8000
```

## Development

```bash
uv sync --all-groups               # backend deps
uv run pytest                      # tests + coverage gate
uv run ruff check . && uv run mypy # lint + types
uv run uvicorn feedelio.api.app:create_app --factory --reload

cd frontend
npm install
npm run dev                        # http://localhost:5173, proxies /api to :8000
npm test && npm run build
```

Install the git hooks once with `uv run pre-commit install`.

## Layout

| Path | What lives there |
|---|---|
| `src/feedelio/core/` | Domain layer over `reader` — the contract everything else uses |
| `src/feedelio/api/` | FastAPI JSON API + serving the built SPA |
| `src/feedelio/worker/` | Scheduled polling loop, its own process |
| `frontend/` | React + TypeScript + Vite SPA |
| `e2e/` | Playwright smoke tests against the real stack |
| `docs/architecture.md` | Decisions record — read this before adding anything |

Configuration is environment-driven (`FEEDELIO_DB_PATH`, `FEEDELIO_STATIC_DIR`,
`FEEDELIO_POLL_INTERVAL`); see `src/feedelio/config.py`.

## Roadmap

Work is tracked as milestones M0–M6 with one issue per feature; the
[tech tree](https://claude.ai/code/artifact/8f982c40-016c-4df3-8251-c897bdcc0f21) shows the
dependency order and effort for all 61 issues.
