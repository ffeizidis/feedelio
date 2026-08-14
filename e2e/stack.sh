#!/usr/bin/env bash
# Boot the real stack for the e2e suite: built SPA + API + a seeded library.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${E2E_WORKDIR:-$ROOT/e2e/.stack}"

# Always rebuild, so the suite can never run against a stale bundle.
# CI builds the SPA in an earlier step and sets E2E_SKIP_BUILD=1.
if [ "${E2E_SKIP_BUILD:-0}" != "1" ]; then
  echo "building the SPA..." >&2
  (cd "$ROOT/frontend" && npm run build)
fi

rm -rf "$WORK"
mkdir -p "$WORK"
cp -r "$ROOT/frontend/dist" "$WORK/static"

export FEEDELIO_DB_PATH="$WORK/feedelio.sqlite"
export FEEDELIO_STATIC_DIR="$WORK/static"
export FEEDELIO_FEED_ROOT="$ROOT/tests/fixtures"

# Seed the library so the smoke test sees data that came out of the database.
uv run --project "$ROOT" python - <<'PY'
from feedelio.config import get_settings
from feedelio.core import make_core

with make_core(get_settings()) as core:
    core.reader.add_feed("sample.atom", exist_ok=True)
    core.update_feeds(scheduled=False)
    print("seeded:", core.status())
PY

exec uv run --project "$ROOT" uvicorn feedelio.api.app:create_app \
  --factory --host 127.0.0.1 --port "${E2E_PORT:-8791}"
