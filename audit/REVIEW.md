# Independent whole-codebase review

Date: 17 September 2026. Baseline: `91e7ff3`.

A dedicated specialist reviewer agent inspected the backend core, API, worker, MCP server, reader adapter, content processing, database/schema, backup/restore, React UI, Chrome extension, deployment files, dependencies and tests. It reported the fourteen findings below, including isolated reproductions for concurrency, rule ordering and stale enclosures. The primary agent implemented and tested the fixes. The reviewer supplied follow-up advice on the transport hooks but could not complete a second review of the final patches; this is not an independent post-fix approval or a security certification.

## Findings and disposition

All fourteen findings are addressed in this revision.

| ID | Severity | Original defect | Repair and regression evidence |
| --- | --- | --- | --- |
| R1 | P1 | Feed cookies reach cross-origin transcript/article/backfill URLs; permanent migration retains credentials; redirect checks ignore scheme. | Credentials bound to scheme, host and effective port; secondary requests and reader requests remove mismatched cookies; permanent cross-origin migration clears the stored cookie. Real HTTP tests cover transcripts, extraction, backfill, migration and scheme mismatch. |
| R2 | P1 | Remote clients can forge `Host: localhost` to bypass the no-token guard. | Tokenless access requires both a loopback hostname and loopback peer. Remote/forged-host/forwarded-header tests reject access. Docker Compose now requires a token. |
| R3 | P1 | DNS is checked before transport resolves again, leaving a rebinding gap. | HTTPcore and urllib3 TCP adapters connect to validated numeric addresses while retaining original Host/SNI/certificate verification. All DNS answers must pass. Tests simulate changing/mixed answers and run real HTTPS against a temporary CA, including wrong-host rejection. Ambient proxies are ignored; explicit proxies require the private-network opt-in and are a documented trusted boundary. |
| R4 | P1 | Extraction, ingestion, rule application and refresh can overwrite concurrent user state/title/interval edits. | Network work stays outside locks. Article operations read current state under an immediate write transaction; refresh uses current custom-title/interval settings. Paused-fetch tests make edits through another Core connection; lock probes verify ingestion and rule updates serialize. |
| R5 | P1 | Retention can delete and tombstone an article starred/marked unread after candidate selection, or ignore a new archive setting. | Current feed options/settings and eligible articles are selected under the same write lock as deletion. Existing retention protection tests plus a competing-write probe cover the boundary. Cold reader caches are tolerated when removing cached entries. |
| R6 | P1 | Opposing concurrent folder moves can create a cycle and hang recursive traversal. | Lock before parent validation and modification; deletion follows the same discipline. Recursive traversal uses `UNION` defensively. Two concurrent opposing moves must allow only one. |
| R7 | P1 | Mark-this-stream in Search/Saved/History marks the entire library; tag streams error. | UI passes view/query/tag/feed/folder scope; core applies the same filters and captures one transactional undo snapshot. Backend and real-browser cases verify unrelated unread items survive and undo restores exactly the affected set. |
| R8 | P2 | Toggling an existing rule changes rowid and execution order. | UPSERT updates in place. A tag-then-read dependency test verifies order and result after disable/re-enable. |
| R9 | P2 | Same-GUID enclosure and source metadata changes are ignored when body is unchanged. | Ingestion updates enclosure lists and publisher metadata independently of content fingerprint; extracted canonical URLs and user state survive. Rotating/removing enclosures and canonical preservation tested. |
| R10 | P2 | Shrinking unread results strands page two with no way back. | Clamp offsets when results shrink; show pagination whenever offset is nonzero. Browser test opens the final item on page two of 101 unread entries and returns to the remaining 100. |
| R11 | P1/P2 | Restore emptiness check races subscriptions; commit failures leave media; export races episode unlinking. | Emptiness and writes are guarded by one immediate transaction, file cleanup includes commit failures, and export/deletion share the write-lock boundary. Tests inject commit failure and probe competing writes during emptiness/media access. |
| R12 | P2 | Canonical resolution changes the base for relative images, links and captions. | Keep fetched document URL as resource base and canonical URL as metadata. AMP fixture verifies the correct paths for all three resource types. |
| R13 | P2 | Missing/malformed optional captions discard otherwise successful article extraction. | Isolate optional transcript failures while retaining a usable body. Tests cover missing tracks and malformed caption metadata. A source with neither body nor usable transcript still fails clearly. |
| R14 | P2 | Backfill interprets plain-text feed content as HTML and loses literal tags. | Use the same content-type-aware escaping as refresh. JSON Feed backfill preserves `Use <widget> here & enjoy.` literally. |

## Verification

The expanded regression suite passed **84 tests** in 31.89 seconds, with four existing dependency/parser warnings. The real-browser suite passed **43 scenarios**, with no failures or skips, in 148.98 seconds. Frontend build, Ruff and whitespace checks pass. See the [result snapshot](review-results.json).

Docker image `feedelio:review-fixes` (`8aca7e005792`) built successfully. An automatically removed, network-isolated container with memory-only data passed frontend, authenticated API, anonymous-request rejection, independent worker-heartbeat and CSP smoke checks. The running user container remains unchanged; the fixes are not deployed there.

New targeted tests are in `tests/test_review_fixes.py`, `tests/test_network.py`, and `audit/test_review_regressions.py`. Existing assertions were not removed or weakened; tokenless TestClient tests now explicitly declare a loopback peer instead of using the synthetic `testclient` address.

The architecture remains unchanged: only `feedelio.core` accesses reader, HTTP/MCP remain adapters, and the worker remains an independent process. No running user container or library was used for these tests.

## Deployment changes and limits

- Before upgrading Docker/proxy deployments, configure a strong `FEEDELIO_TOKEN` and retain it for login and extension configuration. Direct loopback source installs can remain tokenless. The current running container is not automatically replaced.
- Feed credentials do not follow a change of origin. Configure credentials again only after trusting the new origin.
- Proxies resolve targets outside the local adapters' control. They require `FEEDELIO_ALLOW_PRIVATE_NETWORK=1`, must be trusted, and need their own network egress controls. Keep metadata/internal-network egress restrictions on AWS as defense in depth. Do not broaden Uvicorn's forwarded-header trust to arbitrary clients.
- Backup/export holds a write lock while reading media; large audio libraries can delay writes. Backups still assemble in memory. Use a stopped-container volume snapshot for very large libraries.
- Real disk exhaustion/process death, hostile public DNS infrastructure, production publisher integrations, sustained load and AWS deployment were not certified. DNS-changing unit tests and real local TLS validate the adapters, not every possible network environment. The prior browser audit's external-service limitations still apply.
