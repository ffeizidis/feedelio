#!/usr/bin/env bash
# Run scenario specs against a real Feedelio stack in a real browser.
#
# Boots e2e/stack.sh (built SPA + API + seeded SQLite) on a free port, runs the
# scenarios you name in a throwaway directory outside the repo, and tears the
# stack down again. Nothing is written inside the working tree.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$SKILL_DIR" rev-parse --show-toplevel)"

usage() {
  cat >&2 <<'USAGE'
usage: run.sh [options] SCENARIO.spec.ts [SCENARIO.spec.ts ...]

  --skip-build   Do not rebuild the SPA (fast, but you may test a stale bundle).
                 Safe only when frontend/dist is newer than your frontend changes.
  --headed       Run Chromium headed instead of headless.
  --keep         Keep the run directory even when everything passes.
  -h, --help     This.

Anything after `--` is passed straight to `playwright test`
(e.g. `-- --repeat-each=3`, `-- -g "merged stream"`).
USAGE
}

SKIP_BUILD=0
HEADED=0
KEEP=0
SCENARIOS=()
PW_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --headed) HEADED=1 ;;
    --keep) KEEP=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    --)
      shift
      PW_ARGS=("$@")
      break
      ;;
    -*)
      echo "run.sh: unknown option $1" >&2
      usage
      exit 2
      ;;
    *) SCENARIOS+=("$1") ;;
  esac
  shift
done

if [ ${#SCENARIOS[@]} -eq 0 ]; then
  usage
  exit 2
fi

fail() {
  echo >&2
  echo "run.sh: $*" >&2
  exit 1
}

# -- Preflight ---------------------------------------------------------------

BROWSERS="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
if ! compgen -G "$BROWSERS/chromium-*" >/dev/null && ! compgen -G "$BROWSERS/chromium_headless_shell-*" >/dev/null; then
  fail "no Chromium in $BROWSERS.
  Install it with:  cd $ROOT/e2e && npx playwright install chromium
  '--with-deps' additionally installs system packages and needs sudo; on a box
  where you do not have it, install without that flag and, if Chromium then
  refuses to start on a missing shared library, report that you could not run
  browser scenarios rather than guessing at a verdict."
fi

if [ ! -d "$ROOT/e2e/node_modules" ]; then
  echo "run.sh: installing e2e node modules (one-off)..." >&2
  npm ci --prefix "$ROOT/e2e" >&2
fi

if [ "$SKIP_BUILD" != "1" ] && [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "run.sh: installing frontend node modules (one-off, needed to build the SPA)..." >&2
  npm ci --prefix "$ROOT/frontend" >&2
fi

# A scenario that asserts nothing passes trivially and proves nothing. Refuse
# it here rather than reporting a green run that means "the file parsed".
for spec in "${SCENARIOS[@]}"; do
  [ -f "$spec" ] || fail "no such scenario: $spec"
  case "$spec" in
    *.spec.ts) ;;
    *) fail "scenario files must be named *.spec.ts: $spec" ;;
  esac
  if ! grep -qE '(^|[^a-zA-Z])expect[.(]' "$spec"; then
    fail "$spec contains no expect() — a scenario with no assertions is not a pass.
  Add assertions, or say plainly that the scenario was not verified."
  fi
done

# -- A free port, chosen now ------------------------------------------------
#
# 8791 belongs to the CI e2e suite and 8099/8100 are taken by unrelated things
# on this box. Asking the kernel for an unused port is the only way to be sure
# we are neither colliding with, nor silently reusing, somebody else's server.
PORT="$(
  python3 - <<'PY'
import socket

s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/feedelio-browser-check-XXXXXX")"
mkdir -p "$RUN_DIR/scenarios" "$RUN_DIR/results"
cp "${SCENARIOS[@]}" "$RUN_DIR/scenarios/"
cp "$SKILL_DIR/playwright.config.ts" "$RUN_DIR/playwright.config.ts"
# The specs and the config import @playwright/test; borrowing e2e's modules is
# what keeps this out of the repo and off the network.
ln -s "$ROOT/e2e/node_modules" "$RUN_DIR/node_modules"

cleanup() {
  # Playwright tears its own webServer down; this only catches a hard kill.
  # Match on the port we were given and nothing else: pkill -f takes a regex,
  # so unescaped dots in "feedelio.api.app" would match other people's
  # processes, and this box does run unrelated servers.
  pkill -f -- "uvicorn feedelio\\.api\\.app:create_app .*--port[= ]${PORT}($|[^0-9])" 2>/dev/null || true
}
trap cleanup EXIT

export E2E_PORT="$PORT"
export E2E_WORKDIR="$RUN_DIR/stack"
export E2E_SKIP_BUILD="$SKIP_BUILD"
export BROWSER_CHECK_ROOT="$ROOT"
export BROWSER_CHECK_OUTPUT="$RUN_DIR/results"

echo "browser-check: port $PORT, run dir $RUN_DIR" >&2
[ "$SKIP_BUILD" = "1" ] && echo "browser-check: SPA rebuild skipped (--skip-build)" >&2

PW=("$RUN_DIR/node_modules/.bin/playwright" test --config "$RUN_DIR/playwright.config.ts")
[ "$HEADED" = "1" ] && PW+=(--headed)
[ ${#PW_ARGS[@]} -gt 0 ] && PW+=("${PW_ARGS[@]}")

set +e
(cd "$RUN_DIR" && "${PW[@]}") 2>&1 | tee "$RUN_DIR/run.log"
STATUS=${PIPESTATUS[0]}
set -e

echo
if [ "$STATUS" -eq 0 ]; then
  echo "browser-check: PASS — every scenario asserted and every assertion held."
  echo "  server + test log: $RUN_DIR/run.log"
  if [ "$KEEP" != "1" ]; then
    rm -rf "$RUN_DIR"
    echo "  (run dir removed; pass --keep to inspect it)"
  fi
else
  echo "browser-check: FAIL — evidence kept, do not report this as working."
  echo "  server + test log:  $RUN_DIR/run.log"
  echo "  screenshots/traces: $RUN_DIR/results"
  echo "    (the results/... paths above are relative to $RUN_DIR)"
  echo "  view a trace:       cd $ROOT/e2e && npx playwright show-trace $RUN_DIR/results/<test>/trace.zip"
fi
exit "$STATUS"
