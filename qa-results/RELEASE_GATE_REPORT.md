# Production Release Gate Report

**Verdict: PASS WITH BLOCKED LIVE-PROVIDER COVERAGE** — no P0/P1/P2 product defect remains open in executed coverage. Live-provider execution was not available and is reported separately rather than treated as a passing result.

## Coverage

| Metric | Executed result |
|---|---:|
| YAML files discovered under `workflows/` | 535 |
| Non-workflow collection configs excluded | 2 |
| Workflow definitions retained in matrix | 533 |
| Current product-library workflows | 34 |
| Current workflows strict zero-token preflight | 34 / 34 PASS |
| Current workflows live/provider executed | 0 / 34 BLOCKED |
| Real top-level modes | 4 (`studio`, `knowledge`, `eval`, `cost`) |
| Machine-readable workflow-mode rows | 2,132 |
| Persisted runs counted read-only | 211 |
| Persisted private Chat workflows | 0 |
| Persisted run-chat conversations | 0 |
| Synthetic workflow cards browser validated | 400 |
| Synthetic runs browser validated | 120 |
| Chromium viewports | 6 |
| Passing browser case/viewports | 30 |
| Failing browser reproductions | 0 after fixes |
| Backend tests | 2,006 passed, 2 skipped |
| Frontend tests | 418 full-suite + 8 focused regressions passed |
| Screenshots | 24 |

## Workflow Results

- All **34 current root/product workflows** pass strict preflight with no warnings.
- Across all 533 workflow definitions, **496 pass** and **37 fail** the warnings-as-errors gate.
- **25 immutable, non-product-visible Builder snapshots are incompatible** with current preflight rules. They are preserved for audit/preview; selecting one runs compatibility preflight, labels it `Incompatible`, disables restore in the UI, and remains authoritatively blocked with HTTP 422 by the restore API.
- **12 files are warning-only** under the strict gate.
- Evaluation, Cost, and Knowledge are product modes, not alternate workflow execution engines. Those workflow/mode rows are explicitly `NOT_APPLICABLE` rather than falsely reported as executed.

## Bugs

### P0

- None found in executed coverage.

### P1

- **QA-001 FIXED — Business Chat transcript was lost on refresh.** Added owner-scoped durable conversation/message APIs, idempotent message persistence, workflow run correlation, and reload hydration. The retained Chromium regression restores the transcript exactly once at 1920×1080 and 390×844 without launching a duplicate run.

### P2

- **QA-002 FIXED — incompatible immutable Builder history could appear restorable.** Version preview now returns compatibility diagnostics, labels incompatible history, and disables restore before mutation. The restore API continues to reject invalid snapshots with 422. Immutable YAML is not rewritten and current preflight is not weakened.
- **QA-003 FIXED — Workflow Library API failure had no in-place recovery.** Added Retry through the existing loader. Component coverage and six-viewport browser recovery cases pass.

### P3

- None confirmed.

## Performance Findings

- A 400-card Workflow Library loaded, searched, emptied, restored, and rendered without document-level horizontal overflow across all six viewports. Browser cases completed in approximately 2–5.3 seconds under concurrent test load.
- A 120-record Run History filtered and deep-linked successfully across all six viewports, including use of the intended collapsed-panel control below 1150 px.
- Twenty rapid mode changes settled on the intended mode without unexpected console errors or request storms.
- Production initial JavaScript is **269.65 KB** (**83.65 KB gzip**); Builder and Business Chat remain separately chunked.

## UI Findings

- Workflow Library, Run History, and Business Chat had no document-level horizontal overflow at 1920×1080, 1440×900, 1280×720, 768×1024, 390×844, or 360×800.
- Run History intentionally starts collapsed below 1150 px and can be expanded successfully.
- Business Chat restores durable transcripts exactly once after refresh on desktop and mobile.

## Regression Fixes

- Added a Workflow Library Retry action without changing its API or architecture.
- Added `ui/src/modes/studio/Library.test.tsx` for failure → Retry → successful empty state.
- Added Playwright release-gate infrastructure with six viewport projects, controlled API failures, scale datasets, screenshots, traces, console checks, and mode/navigation tests.
- Added a complete workflow matrix generator and JSON/CSV artifacts.
- Added durable owner-scoped Business Chat conversations and refresh restoration.
- Added current-runtime compatibility metadata and restore quarantine for immutable Builder versions.

## Remaining Risks / Blocked Coverage

- No isolated live-provider environment was identified. The 34 current workflows were **not** executed against live LLM, MCP, email, database, web-search, knowledge, MinIO, or other side-effecting dependencies.
- The local database contained zero existing private Chat workflows, so 100 deterministic private workflows and their durable conversation records were exercised through an isolated browser fixture without mutating MongoDB.
- Existing 211 runs were counted read-only. Their private inputs and outputs were not exported or destructively mutated.
- Only Chromium was exercised. Firefox, WebKit, true multi-tab persistence, and live cross-provider concurrency remain unexecuted.

## Artifacts

- `qa-results/workflow-matrix.json`
- `qa-results/workflow-matrix.csv`
- `qa-results/release-gate-summary.json`
- `qa-results/playwright-report/index.html`
- `qa-results/screenshots/`