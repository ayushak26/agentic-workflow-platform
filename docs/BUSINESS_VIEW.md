# Business View — a business operating surface, not a run viewer

The Business View used to be a nicer-looking re-labelling of workflow
execution: a status line derived from the latest event, a "What I understood"
panel that printed the extraction node's raw and parsed JSON, and a timeline
of every node's `started`/`completed` pair. That is a technical execution view
wearing business clothing.

This redesign replaces the projection and the screen. It is **general** — it
works off platform contracts every workflow already has (`execution_kind`,
`RouterAgent`/`DecisionAgent` output shapes, extraction node output, cost
ledger entries), not anything specific to the pump-manufacturer CRM workflow
used as the worked example throughout this document and its tests.

The governing question for every part of the projection is:

> What is this request? What did the system understand? What's missing?
> What did it decide, and why? What can I do about it right now?

---

## 1. Where it lives

```
app/workflow/business_view/
  models.py          Pydantic contract: BusinessProjection and everything in it
  common.py          Pure formatting/classification helpers, no I/O
  runstate.py         RunView/NodeView — one normalised view of a run
  routers.py          Reading a RouterAgent's own config to recover its subject
  activities.py       Node → business activity aggregation
  understanding.py    "What I understood" — business fields, never JSON
  decision.py          The handling decision, its facts, and its rules
  attention.py         The Attention Center — gaps + resolution actions
  context.py           Attachments and related records
  status.py            Deterministic status + "what happens next"
  timeline.py           One entry per business-meaningful moment
  narrator.py            BusinessStatusNarrator — validated, cached, optional
  explanation.py          "Why?" — facts+rules, optionally reworded
  actions.py               Typed, permission-checked BusinessAction factory
  dispatch.py               Executes one typed action server-side
  store.py                   Notes, route overrides, narration cache
  projection.py               Assembles all of the above into one payload

app/workflow/business_projection.py   # re-exports build_business_projection
                                       # (kept as the stable import path)

app/api/runs.py         # /business-projection, /business-narration,
                         # /business-explanation, /business-technical/{id},
                         # /business-action, /fact-correction

ui/src/modes/studio/business/
  useBusinessProjection.ts   fetch + burst-coalescing + 429 backoff
  useBusinessActions.ts       dispatch a typed action, or open a form for one
  panels.tsx                   StatusHero, AttentionCenter, DecisionCard, …
  primitives.tsx                 Card, ActionButton, FactGrid, SourceBadge, …
  dialogs.tsx                     Action prompts, outcomes, TechnicalDrawer
  fixtures.ts                      Shared BASF-RFQ fixture for tests

ui/src/modes/studio/BusinessView.tsx   # assembles the screen
```

`build_business_projection()` is pure and read-only: it takes a run document,
an optional parsed `WorkflowSpec`, an optional pending gate, and optional cost
ledger entries, and returns a `BusinessProjection`. Nothing about it performs
I/O or mutates a run — the API route is the only layer allowed to touch a
database, which is what keeps the whole projection testable with plain dicts.

---

## 2. The core idea: activities, not events

A run with thirteen technical nodes does not do thirteen things a business
user cares about. Four routers answering "is this safe / is production
stopped / how complex / what kind of request" are one activity: *the system
worked out how to handle this*.

`activities.py` performs that collapse. Every node that ran is assigned to
exactly one business activity, in order of authority:

1. **The workflow says so.** A node carrying `experience.stage_id` (with a
   workflow declaring that stage) is placed in that stage — authored
   presentation metadata always wins.
2. **Its `execution_kind` says so.** Every node type declares this
   (`app/nodes/categories.py`): `human`, `input`, `output`, `external`, `ai`,
   `deterministic`. This is a platform contract, not a heuristic — it already
   encodes the distinction a business user cares about (a person decided it,
   a system of record answered it, a model interpreted it, a rule computed
   it).
3. **Its role within `deterministic`.** Only here is a node id consulted, and
   only to separate "who owns this account" routing from "what kind of case
   is this" checks — a *grouping* label, never a routing decision.

The built-in activity buckets: `receive`, `understand`, `enrich`, `handling`,
`ownership`, `ai_work`, `outcome`, `deliverable`, plus one per declared
`experience` stage and one per human-in-the-loop node (never merged — a human
checkpoint is always individually visible).

Only nodes that **actually ran** become activities. A workflow with a hundred
branch endpoints produces three or four activities for one run, because the
ninety-six branches nobody took are not things that happened.

### Reading a router as a business finding

A `RouterAgent` records its branch, its reason, and whether it fell back to a
default — but "primary_department_router → SALES" is not a sentence anyone
wants to read, even though "Primary department: SALES" is exactly what a
salesperson needs.

`routers.py` recovers the *subject* of a router's question from its own
configuration: every condition a router can test names a dotted state path
(`routing_decision.decisions.primary_department`). When every condition on a
router tests the *same* path, that path is the router's subject, and its
actual value in the run becomes the displayed fact. A router whose conditions
test several different things just reports its branch name instead — nothing
is guessed from node names.

This is why the projection works for *any* workflow using `RouterAgent`
in `field`, `rule`, or `conditions` mode, not only the pump-routing example —
and it is why it kept working unmodified when that workflow was later
restructured from a chain of routers into a
`DecisionAgent`-plus-field-mode-router graph (see the worked example below):
the mechanism reads configuration, not node names.

---

## 3. What I understood — business fields, never JSON

`understanding.py` finds the AI node that interpreted the incoming request
(the one whose output carries a `parsed` or `result` dict), and turns its
payload into a list of `BusinessFact`s:

- Machinery fields (`raw`, `confidence`, `missing_information`,
  `english_summary`) are excluded — they feed the summary and the attention
  centre, not the fact grid.
- Nested objects are dropped, not flattened into JSON — printing
  `{"a": 1}` at a salesperson is exactly what this redesign removes.
- A field a person has since corrected is attributed to them
  (`source: human`, `"Corrected by a person"`), not to the model.
- `editable_fields()` marks every scalar/list field as correctable in place —
  a business user should not need to know which rule reads a value to be
  allowed to fix it.

`app/workflow/fact_corrections.py::derive_dependencies()` reads a workflow's
own `RouterAgent`/`DecisionAgent` conditions to work out which decisions a
given extraction field feeds — so correcting `technical_complexity` on the
pump-routing workflow flags whichever router/decision node reads it as stale
automatically, with **no hand-maintained map** for that workflow.
`FACT_DEPENDENCIES` still exists as a hand-written table for
`crm_aware_customer_triage.yaml`, whose `DecisionAgent` sets *named decision
fields* the spec inspection can't recover — both paths feed the same
`apply_fact_correction()`, whose
`node_id`/`payload_key`/`allowed_fields`/`stale_decisions` are now parameters
instead of assumptions baked in for one workflow.

---

## 4. The handling decision — facts, rules, and why

`decision.py` looks for a business outcome in three places, in order of
directness:

1. **The terminal handoff note.** These customer-operations workflows end
   every branch in a `DataTransformAgent` that formats a short note to the
   receiving team (`CASE: STANDARD RFQ\nPrimary team: Inside Sales\n…`).
   `common.py::parse_handoff_note()` reads the `CASE:`/`PRIORITY:` header and
   leading `Key: value` lines — this is the workflow author's own statement
   of the decision, so it wins.
2. **A `DecisionAgent`.** Named decision fields plus its rule trace, for
   workflows that decide via named fields rather than routes.
3. **The routers that ran.** Branches and matched conditions become the
   supporting rules; their recovered subjects become the supporting facts.

A person can override the decision (`route_override` action). The card then
shows `overridden: true`, `original_headline`, and who changed it — a human
decision is never silently presented as the system's own.

### Why? — grounded, optionally reworded

The deterministic explanation (facts + matched rules, verbatim from the run)
is always correct and always available. `explanation.py` may hand it to a
small model to turn into one readable paragraph, but only accepts the result
if **every fact/rule id it cites actually exists** in what it was given — an
explanation citing something absent, or citing nothing at all, is discarded
and the deterministic form is shown instead. Chain-of-thought is never
requested or displayed.

---

## 5. The Attention Center

`attention.py` turns `missing_information` into resolvable gaps (never a bare
field-name list). For each gap it asks, in order:

1. **Is there a real attached file?** (`context.py::build_attachments()` only
   lists `WorkflowFileRef`s the run actually has — never a document merely
   *mentioned* in the customer's prose.)
2. **Does a reference the customer already gave point at a system of
   record?** (`context.py::build_related_records()`, gated on whether this
   run's own workflow actually calls a matching MCP tool/server —
   `available_tools()` reads that from the spec, so a lookup button is never
   offered for a connection the platform doesn't have.)
3. **Can the person just type it?** (only if the field is one the run's
   extraction actually exposes).
4. **Does the customer need to be asked?** (`draft_clarification` — always
   marked `requires_approval=True`; the platform drafts, a person sends).

Severity: a gap in a field a router/decision actually reads is `warning` (it
changed, or could still change, how the case is handled); one no rule reads
is `info`. Within a severity, gaps with an evidence-backed action (open a
document/record) rank above ones that only lead to asking the customer.

Other attention sources: a system-of-record check that errored or timed out
(`"Could not check …" / "No changes were made."` — never silent), multiple
matching customer accounts, and stale decisions after a correction.

---

## 6. Status, next step, and provenance

`status.py::build_status()` is entirely deterministic — run status, pause
kind, which activity is active, whether a handling decision exists. A model
is never consulted for `code` or `tone`. `build_next_step()` always answers
"what happens next", blocked or not, with the actions that unblock it.

`status.py::state_version()` hashes everything a business user would *notice*
changing (status code, attention items, decision headline, activity
statuses, understood facts) — explicitly excluding timestamps, durations and
costs, so a run that merely took longer does not get re-narrated.

### Provenance (`§21–§25` in the original brief)

Every `BusinessFact`/`BusinessDecisionView` carries a `BusinessSource`
(`ai | rule | system | human | customer_message | workflow`), derived from
the producing node's `execution_kind` — never guessed. An AI-sourced badge
names the model that **actually executed**
(`AIModelUsage.executed`, from `node_runs[...].model_selections` and cost
ledger entries), separately from `requested`/`selected`, so provider fallback
is visible without misrepresenting what ran. A rule-based or ERP-sourced
result never carries a model badge at all.

### BusinessStatusNarrator

`narrator.py` may rephrase the deterministic headline/summary into plainer
English. Bounded input only (`NarrationInput` — status, decision, a handful
of facts/gaps/pending actions, **no raw payload, no node ids**), small/cheap
model (`business_status_narration` capability), low temperature, capped
tokens. `validate()` extracts every capitalised/numeric "claim" token from
both the input and the output and rejects the narration if the output claims
anything the input didn't say. On rejection, unavailability, or any
exception, `deterministic_narration()` — the same headline/summary already on
screen — is used; the screen never depends on the model running at all.

Narration is fetched via a **separate** endpoint
(`POST /business-narration`), cached by `state_version` in a small
`business_narrations` Mongo collection (`store.py`), so repeated polls or
renders never re-spend a model call.

---

## 7. Typed actions — a closed, server-checked vocabulary

A Business View button never carries a URL or a free-form prompt. It carries
one `BusinessActionType` (`models.py`) plus validated params. `actions.py`'s
`ActionFactory` builds only the actions valid **now**, for **this person**
(permission via `has_permission(role, "workflow:run")`, state via the run's
own status/gate/decision) — a control invalid for the current state is simply
never emitted.

Three groups of action, by how they're carried out:

- **Client-side** (`open_technical_details`, `open_related_record`,
  `document_review`, `ask_ai`) — the UI performs these itself.
- **Delegated to an existing, already-audited endpoint** (`pause_run`,
  `resume_run`, `stop_run`, `approve`/`reject`, `assign_work_item`,
  `edit_fact`, `rerun_dependency` → `retry`/`restart`) — `dispatch.py`
  explicitly refuses these and names the route that owns them, so there is
  exactly one audited way to do each thing.
- **Dispatched here** (`add_note`, `route_override`, `draft_clarification`,
  `related_record_lookup`) — `dispatch_business_action()` is the only place
  that implements these.

`POST /api/runs/mine/{run_id}/business-action` re-derives the projection
server-side and only accepts an action type that projection actually
offered — the rendered screen and the accepted commands cannot disagree.

`draft_clarification` drafts a customer email via a small model
(`clarification_drafting` capability) and **never sends it** — the response
says so explicitly (`"sent": false`). `related_record_lookup` only exists for
tools/servers the run's own workflow already declared, calls them read-only
(`approval_satisfied=False`), and reports "nothing was changed" on failure.

---

## 8. The timeline

`timeline.py` emits one entry per **business-meaningful moment** — an
activity completing (with its facts as "✓ …" marks), the routing decision,
and, always individually (never collapsed): human reviews, fact corrections,
route overrides, and failures. Node `started`/`completed` pairs never reach
it. A 26-event technical run typically collapses to 6–9 timeline entries.

---

## 9. The API surface

| Route | Purpose |
| --- | --- |
| `GET /api/runs/mine/{id}/business-projection` | The whole screen. Pure, cheap, no raw payloads. |
| `POST /api/runs/mine/{id}/business-narration` | Rephrase status; cached by `state_version`. |
| `GET /api/runs/mine/{id}/business-explanation` | "Why?" — grounded, lazy. |
| `GET /api/runs/mine/{id}/business-technical/{activity_id}` | Raw output, prompts, per-node payloads — the **only** route that returns them. `activity_id="run"` returns every node. |
| `POST /api/runs/mine/{id}/business-action` | Typed action dispatch, server-checked against the current projection. |
| `POST /api/runs/mine/{id}/fact-correction` | Generalised: node/payload-key/allowed-fields/stale-decisions come from the run's own workflow. |

`app/workflow/business_view/store.py` adds one Mongo collection
(`business_narrations`, indexed on `run_id+session_id+state_version`) and two
`run_history` array fields (`business_notes`, `route_overrides`) — no new
source of truth about a workflow, just records of what a person did.

---

## 10. Frontend refetch behaviour (the 429 fix)

The previous `BusinessView` re-fetched the projection once per SSE event —
a 13–14-node run emits ~26 `node_started`/`node_completed` events in a few
seconds, which blew through the platform's 60-req/min rate limit and then
kept spending that budget on 429s.

`ui/src/modes/studio/business/useBusinessProjection.ts` fixes this with three
rules, applied in order:

1. **Coalesce.** Non-first fetches wait out a 700ms burst window before
   firing once.
2. **Never overlap.** A refetch requested mid-flight is queued, not fired
   again; only `node_completed`/`node_reused`/`node_paused`/run-terminal
   events count as "significant" (`node_started` is excluded — it changes no
   business fact).
3. **Back off on 429, don't hammer.** Backoff starts at 15s and doubles up to
   60s per consecutive rejection; the last good projection stays on screen
   with a `throttled` banner rather than being replaced by an error.

`useBusinessNarration()` is keyed on `state_version` and fires at most once
per distinct value, regardless of render count.

---

## 11. Testing

- `tests/business_view_fixtures.py` — the BASF RFQ from the worked example,
  as a real run document against the real
  `workflows/pump_manufacturer_case_routing.yaml` spec (no workflow mocking).
- `tests/test_business_projection.py` (50 tests) — activity collapse, no raw
  JSON anywhere in the payload, deterministic status for every run state,
  attention resolution actions, decision facts/rules, AI vs. rule vs. system
  provenance, permission gating, degrading gracefully with no/foreign
  workflow spec — including the older `multilingual_customer_request_triage`
  workflow, to confirm none of this is pump-specific.
- `tests/test_business_narrator.py` — bounded input, rejection of
  unsupported/over-long output, silent fallback on model failure/absence,
  cache-key stability; the same pattern for `explanation.py`.
- `tests/test_business_actions.py` — state/permission gating on every
  factory method, dispatch refusing unknown/delegated/client-side types,
  notes/overrides/clarification drafts/record lookups.
- `tests/test_business_view_api.py` — the HTTP surface: no raw payload in
  `/business-projection`, raw payload present only in
  `/business-technical/*`, narration caching across two calls, action
  rejection when not offered by the current projection.
- `tests/test_fact_corrections.py` — `derive_dependencies()` against the real
  spec, and the generalised `apply_fact_correction()` targeting an arbitrary
  node/payload key.
- `ui/src/modes/studio/BusinessView.test.tsx` (31 tests) — first-screen
  content, no-raw-JSON assertion against the DOM, attention actions, model
  provenance badges, activity expansion, typed-action dialogs (route
  override, clarification draft), fact editing, SSE burst coalescing, and
  429 backoff behaviour.

Run everything with:

```bash
python -m pytest tests/test_business_projection.py tests/test_business_actions.py \
  tests/test_business_narrator.py tests/test_business_view_api.py \
  tests/test_fact_corrections.py -q

cd ui && npx vitest run src/modes/studio/BusinessView.test.tsx
```
