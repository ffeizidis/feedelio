# Wide desktop layout and no-login verification — 2026-09-18

Cause: the `min-width: 1600px` media query still defined two grid columns after the divider became a third grid child. The reading pane was auto-placed in a second row under the sidebar. The original tests only used 1440px and asserted DOM containment, not horizontal placement.

Before the fix, real Playwright Chromium at 1900px reproduced the defect: reader top was 455px, not 0. Removed the stale column override, leaving one resizable three-track grid at every desktop width.

Verification:

- `.venv/bin/pytest tests audit -q`: **150 passed**, four existing warnings, 204.67 seconds.
- Layout regression checks at 1440, 1599, 1600, 1900 and 2560px verify actual bounding boxes, full-height alignment, source/folder lists, opened articles, mouse-wheel scrolling, visible scrollbar gutters, pointer/keyboard resizing and persisted display controls.
- Ruff and frontend production build passed.
- Deployed image `feedelio:wide-no-login`, image ID prefix `e9bd16e172ee`; healthy API and independent worker, existing `feedelio-data` volume, port published only on `127.0.0.1:8000`.
- `audit/check_live_layout.py` passed against the deployed container in a fresh Chromium context without credentials: all five desktop widths, dragging, title/preview controls, opening an already-read article, scrolling, returning to source/folder lists and reload. No JavaScript errors or Feedelio session cookies. UI preferences restored; the normal article-open history event remains.
- Live screenshots and geometry results: `test-results/live-wide-no-login/` (ignored, contains personal library content). Visually inspected the article screenshot at 1900 × 1018.

No-login mode is explicitly enabled with `FEEDELIO_NO_AUTH=1` (the local Compose default); it overrides an old token. Cross-origin mutation and non-loopback Host checks remain active. This mode relies on localhost-only network binding and must not be exposed through an unprotected public proxy. Optional token authentication remains available for remote deployments with `FEEDELIO_NO_AUTH=0`.

The first live smoke run passed the layout checks but its assertion against **all domains'** cookies also counted publisher image cookies. Corrected the assertion to Feedelio's origin and reran successfully.
