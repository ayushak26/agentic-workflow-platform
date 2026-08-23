# Eurskem AI — Portfolio Bundle

Documentation package for the Eurskem AI agentic workflow platform, illustrated throughout with
screenshots captured from the running application.

| | |
|---|---|
| **Owner** | Ayush Khandelwal |
| **Repository** | `github.com/ayushak26/agentic-workflow-platform` |
| **Code snapshot** | `main` at `7ca16b2` — 3 August 2026 |
| **Screenshot basis** | Local instance of `7ca16b2`, captured 9 August 2026 |
| **Bundle date** | 9 August 2026 |

---

## Contents

```
portfolio/
├── README.md                                        this file
├── Eurskem_AI_Technical_Portfolio_Reference_v2.pdf  41 pp — architecture and engineering reference
├── Eurskem_AI_Walkthrough_Transcript_v2.pdf         24 pp — 10-minute presenter script and shot list
├── screenshots/                                     25 PNGs, 3360 × 2100, unretouched
└── build/                                           HTML + CSS sources for both PDFs
    ├── portfolio.html
    ├── transcript.html
    ├── style.css
    └── transcript.css
```

### 1. Technical Portfolio Reference (41 pp)

The engineering reference. Eleven sections plus three appendices, covering the truth boundary, users
and product thesis, the canonical Workflow surfaces, the runtime boundary, the workflow contract and
zero-token preflight, evidence integrity, model routing and cost, security, deployment, testing and
failure modes, and an interview walkthrough.

Every code sample is quoted from `7ca16b2` with its file and line range. Every screenshot is a live
capture. Figures 1–5 are inline SVG.

New in this revision, beyond the addition of screenshots:

- **§7 — a real cost ledger.** The complete `cost_ledger` for the demo run, read from MongoDB:
  9 nodes, 12 calls, $6.6495, with one genuine provider fallback (`outline` requested
  `claude-sonnet-4-5`; `gpt-5.6-terra` ran).
- **§6 — the fail-closed boundary, observed.** An evidence-stage run that examined 19 critical
  claims and verified **zero**, returning 19 blocking issues rather than 19 plausible citations.
- **Appendix A — a worked example.** One 14-node run end to end: input hash, per-node durations,
  cost, the rendered 25-page deliverable, and what the run does *not* prove.
- **Appendix C — screenshot index and capture method**, so every image is reproducible.

### 2. Walkthrough Transcript (24 pp)

A presenter script for the 10-minute video. Nine time-banded beats, each with three fixed blocks:

- **On screen** — what to have open before speaking
- **Say** — the words, verbatim (~1,300 words total, 125–135 wpm)
- The supporting screenshot, code snippet, or run record

Amber **presenter notes** flag three places where the original script promised something the current
build does not surface, and give the honest alternative:

1. **Per-node cost is not in the UI.** The Cockpit node inspector says so itself. Beat 7 reads the
   ledger directly and says where the number comes from.
2. **A fallback record genuinely exists**, so beat 7 shows a real one rather than a hypothetical.
3. **The demo run has no verified claims to trace** (`Sources (0)` — its evidence base is bounded
   Deep Research dossiers). Beat 6 uses a run that did exercise the acquire/verify chain.

### 3. Screenshots (25 PNGs)

`screenshots/` — 3360 × 2100 each (1680 × 1050 viewport at DPR 2), PNG, unretouched. No image is
composed, annotated or cropped; the two graph close-ups are the application's own zoom.

| File | Surface |
|---|---|
| `01-workflow-library.png` | Workflow Library, 20 workflows by outcome |
| `02-library-concept-note-card.png` | Library filtered to the flagship workflow |
| `03-guided-run-overview.png` | Historical workflow-progress overview — five stages, attention queue, outputs |
| `04-guided-run-outputs.png` | Historical workflow-progress deliverables tab |
| `06-cockpit-graph.png` | Cockpit — live graph and lifecycle counters |
| `06b-cockpit-graph-fullscreen.png` | Cockpit — full-screen graph |
| `07-cockpit-node-detail.png` | Cockpit — failed node, duration and diagnosis |
| `08-cockpit-output-viewer.png` | Cockpit — output viewer for a completed run |
| `09-builder-canvas-palette.png` | Builder — the four-area layout |
| `10-builder-graph.png` | Builder — whole graph, fitted |
| `10b-builder-graph-fanout.png` | Builder — the five-way drafting fan-out |
| `11-builder-configure.png` | Builder — typed node configuration |
| `12-builder-preflight.png` | Builder — preflight passed, 0 tokens used |
| `13-builder-versions.png` | Builder — immutable version history |
| `14-builder-map-data.png` | Builder — visual data mapping |
| `15-run-history-list.png` | Run History — durable run list |
| `16-run-detail-overview.png` | Run History — run record and routes back in |
| `17-run-detail-nodes.png` | Run History — per-node durations |
| `18-run-detail-timeline.png` | Run History — timeline |
| `19-run-detail-outputs.png` | Run History — outputs |
| `20-cockpit-audit.png` | Cockpit — per-node audit trail |
| `21-cockpit-score.png` | Cockpit — output scoring panel |
| `22-builder-test.png` | Builder — node/branch test panel |
| `23-output-proposal-cover-toc.png` | Rendered deliverable — cover and contents |
| `24-output-proposal-body.png` | Rendered deliverable — methodology body |

---

## The runs these documents cite

| Run | Workflow | Outcome | Used for |
|---|---|---|---|
| `ed462b3e` | Concept Note to 10-Page Methodology Section | Completed, 14/14, 1,413 s, $6.6495 | Workflow progress, Run History, cost ledger, output |
| `3d46a8ee` | same workflow | Failed at `graphnormalizer_1` after 244.9 s | Cockpit live graph, failure diagnosis |
| `1f2c3a4d` | Horizon Europe Part B — Evidence (Stage 1 of 3) | Completed, 14/14, 509.7 s, **0 verified claims** | Fail-closed evidence boundary |

Cockpit switches to the output viewer when a run's status is `completed`
(`ui/src/modes/studio/Cockpit.tsx:370`), which is why the live-graph screenshots come from the failed
run. That is also the honest place to demonstrate failure diagnosis.

---

## Rebuilding the PDFs

Both documents are plain HTML + CSS rendered by WeasyPrint. Image paths are relative, so the
`portfolio/` directory must stay intact.

```bash
pip install weasyprint

cd portfolio/build
weasyprint portfolio.html  ../Eurskem_AI_Technical_Portfolio_Reference_v2.pdf
weasyprint transcript.html ../Eurskem_AI_Walkthrough_Transcript_v2.pdf
```

Edit `portfolio.html` / `transcript.html` for content, `style.css` for the shared print design, and
`transcript.css` for the presenter-script blocks. The palette is taken from the product itself
(`ui/src/styles/globals.css`): navy `#092536`, teal `#007f7b`.

## Reproducing the screenshots

Screenshots were taken with a headless Chromium session driving the app at `localhost:5173` against a
backend at `localhost:8000`.

```bash
docker compose up -d mongo weaviate minio redis   # data services
uvicorn app.main:app --port 8000                  # backend
cd ui && npm run dev                              # UI on :5173
```

Sign in with the development bypass account (`app/config.py:250-252`), then drive the routes:

- `/workflows`
- `/workflow-runs` → select a run → **Open in Cockpit**
- `/builder/<workflow-name>`

Cockpit can receive Workflow YAML through in-app navigation; a direct URL may not carry that navigation
state, so opening a run from Workflow Runs is preferred.

---

## Honesty boundary

These documents are deliberately explicit about what the evidence supports.

**What is demonstrated.** A 14-node graph compiled and executed end to end. Parallel fan-out and
deterministic assembly behaved as authored. Object storage, cost ledger, audit and durable history
were all populated. A provider fallback was recorded rather than hidden. The renderer reported a
constraint breach instead of truncating silently. The evidence gates refused to promote unverified
material when acquisition returned nothing.

**What is not.** These are single runs on one developer machine — not benchmarks, service levels or a
price list. The repository documents an IONOS production topology with readiness gates, rollback and
backups; live production server health was not inspected while preparing this bundle, and deployment
tooling is not a running service. No claim is made about adoption, accuracy, savings, throughput or
regulatory approval. GDPR-oriented controls are not GDPR compliance.

**Known gaps, stated in the documents rather than omitted.** Per-node cost is recorded but not yet
joined into the Cockpit node inspector. End-to-end evidence *acquisition* did not succeed on the input
tested — verification held, source resolution is the weak link, and the evidence-quality gate
described in §6 is the response.

**Maintenance.** Screenshots age faster than prose: a UI change invalidates an image silently, whereas
a code sample at least carries its file and line numbers. If `main` moves, recapture the screenshots
and recheck every code sample before reusing these documents.
