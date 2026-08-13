# Visual AI Workflow and Business Logic Builder

How a new business process is now expressed on this platform, and why almost
none of it should require Python.

The governing question for every extension is:

> Can the user express this new business behaviour by configuring the workflow?

If yes, **no new NodeType**. Prompts, schemas, classification labels, languages,
thresholds, departments and business rules are workflow configuration. A new
NodeType is the exception, reserved for a genuine capability boundary.

---

## 1. Architecture changes

The runtime contract is unchanged. Nothing here is a second engine:

```
Workflow YAML → WorkflowSpec → Preflight → NodeRegistry → LangGraph
              → _make_runtime_fn → Node execution
```

What changed is that the contract became more *expressive*, and three new
modules now sit under it as shared vocabulary:

| Module | Role |
| --- | --- |
| `app/runtime/field_schema.py` | Visual schema rows → Pydantic model, JSON Schema, typed dotted paths |
| `app/runtime/rules.py` | Deterministic rule evaluation with a per-condition trace |
| `app/runtime/logic_preflight.py` | Zero-token validation of authored logic |

The important property is that each of these has exactly one implementation
serving every consumer. The operators the rule editor offers, the paths preflight
authorises, the fields the mapping picker shows, and the schema the model is
constrained by are all derived from the same source — so the editor cannot build
something the validator rejects, and the validator cannot authorise something the
runtime will not resolve.

```
                  FieldSpec rows (what the author edits)
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  Pydantic model      JSON Schema         typed path index
  (validation)        (provider)          (mapping · rules · preflight)
```

New surface: `app/api/builder.py` (authoring API) and
`app/integrations/email/` (provider-neutral email capability).

---

## 2. The reusable primitive node model

Seven core primitives, marked `family: "core"` in the registry manifest and
grouped first in the palette:

| Palette | Type | Kind | What it is |
| --- | --- | --- | --- |
| Input | `WorkflowInputAgent` | input | Information entering the workflow, with a declared shape |
| AI Task | `AITaskAgent` | ai | One configurable AI capability |
| Decision | `DecisionAgent` | deterministic | Business rules, no model call |
| Router | `RouterAgent` | deterministic | Branching, with the reason recorded |
| Transform | `DataTransformAgent` | deterministic | Deterministic data shaping |
| Human Review | `HumanInLoopAgent` | human | A person approves, edits or rejects |
| Email | `EmailAgent` | external | One capability, an operation selector |

`execution_kind` is the second axis and is what makes the automation boundary
visible on the canvas: **ai / deterministic / external / human / input / output**.
It is derived from each node's own `required_services` declaration where
possible, so it cannot go stale when a node starts or stops calling a model.

The 43 pre-existing node types remain registered, runnable and unchanged. They
are classified `family: "specialized"` and appear below the core blocks.

---

## 3. AI Task design

`app/nodes/ai_task.py`. One node replaces the whole family of
`GermanEmailAgent` / `QuotationRequestAgent` / `UrgencyAgent` classes, because
everything that distinguishes them is configuration:

* **task** — extract, classify, summarize, translate, analyze, rewrite, generate,
  draft_response, compare, evaluate, custom. Task semantics live in one
  `TASK_DIRECTIVES` table; adding a task is a line, not a class.
* **instruction** — the author's own words.
* **input / context** — templated references, written by the mapping picker.
* **output_fields** — the visual schema (§5).
* **language** — a policy object, not a separate translation node (§8).
* **model** — `auto` or any catalogue model, through the existing gateway.
* **examples**, **include_confidence**, **fail_on_error**, retries.

### Output contract

```
result              the author's schema, validated
text                free-text output for non-structured tasks
status              ok | refused | invalid_output | provider_error
confidence          promoted, so a gate reads outputs.<step>.confidence
detected_language   promoted from the schema, whatever the author named it
reasoning, model_used, attempts
```

### Structured output enforcement

Provider-native structured output via the gateway's `complete_structured`, never
"return valid JSON". The four outcomes of §7 are distinct facts:

| Outcome | Behaviour |
| --- | --- |
| Valid | `status: ok`, `result` validated against the compiled model |
| Refusal | `status: refused` — distinguished from a schema failure so the author isn't sent to debug a schema the model simply declined |
| Validation failure | Retried with a correction prompt, then `status: invalid_output` |
| Provider failure | `status: provider_error`, **no retry** — retrying a prompt no provider can serve is a certain failure |

With `fail_on_error: false`, a failure becomes `confidence: 0.0` and a routable
status rather than a dead run. Confidence `0.0` and not `None` deliberately: a
`< 0.8` gate against `None` is a silent `False` that reads as "the model was
sure" — the opposite of the truth.

---

## 4. Visual structured-output builder

`app/runtime/field_schema.py` + `ui/.../builder/SchemaBuilder.tsx`.

The author edits rows. One recursive row component covers every depth, so
`equipment → process → flow_rate → {value, unit}` needs no separate editor per
level.

Supported: `object`, `list`, `string`, `text`, `number`, `integer`, `boolean`,
`enum`, `date`, plus `required`, `nullable` and numeric bounds.

Two design decisions worth stating:

* **`required` and `nullable` are independent.** A required-but-nullable field is
  how you tell a model *"always return this key, use null when the source does
  not state it"* — the anti-hallucination contract for extraction.
* **Every edit is compiled server-side** by the same compiler the runtime uses.
  An invalid row is reported while the author is still typing, and the JSON
  Schema shown is the one the model will actually be held to.

`enum` compiles to a `Literal`, so the provider's structured-output mode
constrains generation to the allowed set instead of the model inventing
`tech_support` for `technical_support`.

---

## 5. Business rule engine

`app/runtime/rules.py`. A rule is structured data, not an expression — no
`eval`, and fully round-trippable through the visual editor.

```
Condition       field · operator · value
ConditionGroup  and | or | not, nesting arbitrarily
Rule            name · when · then[] · default · stop_on_match
Action          set | append | increase | decrease
```

Sixteen operators, typed per field kind via `OPERATORS_BY_TYPE` — the same table
the UI reads and preflight enforces.

Three behaviours that matter in practice:

* **All matching rules apply**, not first-match-wins. Business policy is
  additive: "low confidence" and "it's a complaint" are two independent reasons
  that should both appear in the explanation.
* **A missing value fails every comparison**, negative operators included. If
  `not_equals` passed for data that was never extracted, an incomplete request
  would route as though it were complete.
* **Later rules see earlier conclusions** under `decisions.`, so
  "production_stopped → urgency=critical" can be followed by
  "urgency=critical → notify" without another node.

Every evaluation returns a full trace: which conditions were checked, what value
each one saw, and why the outcome followed.

---

## 6. Router UX

Four modes on one node type:

| Mode | Use |
| --- | --- |
| `field` | One branch per value of a classified field — the common case |
| `conditions` | First matching rule group wins — branches depending on several facts |
| `rule` | Legacy string expressions, kept for existing workflows |
| `llm` | Genuinely fuzzy routing; costs tokens |

The two new modes are deterministic, free, and produce an explanation the
Builder renders directly. Branch names appear on the canvas edge. When the routed
field is an enum, the editor offers a one-click "add the N missing values" — the
difference between typing seven intent labels from memory (and misspelling one)
and clicking a button.

Router output gained `route_value`, `explanation`, `matched_conditions` and
`used_fallback` — all optional, so existing workflows are unaffected.

---

## 7. Mapping UX

Clicking a field *is* the mapping. `POST /api/builder/output-contract` returns,
for the selected step, every value that can actually reach it, typed:

```
Understand Customer Request              [AI]
  result.intent          Enum      always set    quotation_request | technical_support | …
  result.equipment.model String    may be empty  Model designation exactly as written
  confidence             Number    may be empty
```

Each entry carries the reference to write (`{{outputs.…}}`), its type, its
description, and whether it can be unavailable at run time (§15) — including the
subtle case where a *required* field inside an *optional* object can still be
null.

Only upstream values are offered, so the picker cannot construct a reference that
would fail preflight.

---

## 8. Test and simulation experience

| Surface | What it does |
| --- | --- |
| **Test this step** | Runs one node against pasted sample data. Real execution, no run record. Config errors, mapping errors and runtime errors are reported distinctly. |
| **Test through this step** | The existing smallest-valid-slice test, unchanged. Never modifies the saved workflow. |
| **Simulate** | Runs the whole workflow in memory through the real runtime, returning a per-step trace with each step's explanation. |

The simulator's **step override** is what makes the decisive demonstration take
seconds: freeze the extraction at `confidence: 0.64`, rerun, and the graph
routes to Human Review instead of Support. It reuses the runtime's existing
reused-node mechanism (the one a retry uses), so a frozen step costs no tokens
and the rest of the graph genuinely executes against it.

A simulation deliberately omits `audit_db` and `event_bus`: no run record, no
run events, no pollution of Run History. A workflow with preflight errors is not
run at all — the report comes back instead.

An external send cannot be triggered from the Test tab. A test is something an
author runs twenty times while adjusting wording; a send is not.

---

## 9. Explainability

Every step reports **what kind of thing decided it**:

```
Understand Customer Request      AI inference
  ↳ intent = technical_support, confidence 0.93

Check Automation Safety          Deterministic rules
  ✓ Stopped production is critical
      outputs.understand_request.result.production_stopped is true
      urgency = 'critical'

Route Customer Request           Deterministic routing
  Branch: technical_support
  ✓ outputs.understand_request.result.intent equals 'technical_support'
```

Rendered identically in the node Test tab and the Simulator, because the
explanation is a property of the step, not of the surface showing it.

---

## 10. Email integration design

```
        EmailAgent  (connection + operation selector)
              │
        EmailService  (permissions · idempotency · audit)
              │
      ┌───────┴────────┬──────────────┐
   Gmail          Microsoft Graph   In-memory
   adapter        adapter           adapter
```

One node replaces `GmailSearchAgent`, `GmailReadAgent`, `GmailSendAgent`,
`OutlookSearchAgent`, … Provider differences — Gmail's `q` grammar and base64
MIME vs. Graph's OData filters, JSON messages and dedicated `/reply` endpoint —
live in adapters below the node contract.

**Workflows reference a connection id, never a token.** Credentials resolve from
the environment by variable *name*, so a workflow can be exported and shared
without carrying mailbox access.

### Side-effect safety (§49)

* Sending is refused unless the connection permits it. A workflow cannot grant
  itself that permission.
* Every write is fingerprinted `sha256(connection, operation, recipients,
  subject, body, thread)`, scoped by run id, and **reserved before** the provider
  call. A retried run deduplicates; two different runs sending the same templated
  reply do not collide.
* An **ambiguous** failure (timeout, 5xx after acceptance) keeps the reservation
  and refuses the retry with a message telling a person to check the mailbox. A
  **definitive** failure (rejected address) frees the key so a corrected retry
  works.

---

## 11. Multilingual and model routing

Language is a policy on the AI Task, not a chain of nodes:

```
Input language          auto
Process in original     yes
Output language         en
Preserve original       yes
```

One call detects, classifies and extracts. Translate-then-extract is both more
expensive and *less accurate*, because translation destroys exactly the tokens
extraction depends on — model designations, part numbers, serial numbers.

Model selection stays provider-independent through the existing gateway and
`ModelRouter`; `auto` plus a routing policy is configured in the Advanced tab,
deliberately away from the business logic.

---

## 12. Human review design

`HumanInLoopAgent` gained `review_panels` and `review_purpose` — labelled blocks
with a business name and a reason each value matters, instead of raw dotted
paths. A panel whose path is absent is shown as *unavailable* rather than
dropped: "confidence: not available" tells the reviewer something real.

`context_fields` remains the fallback, so existing HITL workflows are untouched.

---

## 13. Preflight additions

All zero-token, all in `app/runtime/logic_preflight.py`:

| Code | Catches |
| --- | --- |
| `UNKNOWN_FIELD_REFERENCE` | A rule/route field the upstream step does not produce — with "did you mean" |
| `RULE_TYPE_MISMATCH` | `>=` on a list, a string compared with a number field |
| `INVALID_THRESHOLD` | `confidence >= 80` against a 0–1 field |
| `INVALID_ENUM_VALUE` | A value or branch outside the enum |
| `AI_OUTPUT_NOT_AVAILABLE_UPSTREAM` | Reading a value from a step that does not always run first |
| `REQUIRED_FIELD_MAY_BE_NULL` | A condition on a value that can be null |
| `ROUTER_BRANCH_WITHOUT_TARGET` | A route with no edge |
| `UNREACHABLE_BRANCH` | An edge branch the router can never return |
| `MISSING_DEFAULT_ROUTE` | No fallback, and uncovered enum values named |
| `EXTERNAL_ACTION_WITHOUT_REVIEW` | An email send with no human gate guaranteed before it |
| `ROUTER_JOIN_UNREACHABLE` | Two routers branching into one shared step |

The last one was found by simulating this platform's own example workflow. Two
routers both targeting `human_review` compile to an AND-join neither can satisfy,
so the run reported **completed** while silently skipping the escalation — no
error, no failed node, nothing in the trace. It is now caught structurally,
because a simulation only reveals it on the input that happens to take that
branch.

`INVALID_THRESHOLD` is the other high-value one. `confidence >= 80` meaning 80%
never fires, so every request looks confident and nothing is ever escalated — a
silent failure that survives testing.

---

## 14. Tests

| File | Covers |
| --- | --- |
| `tests/test_field_schema.py` (23) | Schema compilation, nesting, authoring errors, path index |
| `tests/test_rule_engine.py` (38) | Operators, missing values, nesting, explainability, typed catalog |
| `tests/nodes/test_core_primitives.py` (52) | All five new primitives, router back-compat, palette metadata |
| `tests/test_email_integration.py` (29) | Operations, permissions, idempotency, both real adapters via mocked transport |
| `tests/test_logic_preflight.py` (32) | Every new code, plus "the clean workflow warns about nothing" |
| `tests/test_builder_api.py` (37) | Contract/preflight equivalence, node test, simulation, auth |
| `ui/.../rule-rendering.test.ts` (23) | Value coercion, reference round-trip, rule rendering |

**Backend: 880 passing.** The 7 failures in the suite are identical on the
untouched tree (a missing `horizon_proposal_hitl_pdf.yaml`, a candidates parsing
bug, a skill-bundle assertion) — pre-existing and unrelated.

**Frontend: 142 passing**, typecheck and production build clean.

---

## 15. Example industrial workflow

`workflows/multilingual_customer_request_triage.yaml` — 10 steps, **zero
domain-specific node types**:

```
Incoming Request              WorkflowInputAgent
        ↓
Understand Customer Request   AITaskAgent      ← one call: detect + classify + extract
        ↓
Check Automation Safety       DecisionAgent    ← 6 rules, nested AND/OR
        ↓
Route Customer Request        RouterAgent      ← conditions mode, escalation case first
   ┌────┬──────────┬──────────┬─────────────┬──────────────┐
 Sales  Support  Spare Parts  Cust. Service  Human Review
```

Everything specific to customer triage — the schema, the seven intent labels, the
0.80 threshold, the escalation rules, the routing table, the review panels — is
configuration in this one file. Passes preflight with **zero issues**.

---

## 16. Backward compatibility

* No node type removed, no config schema narrowed.
* `RouterAgent` gained modes and optional output fields; `rules`/`llm` behave
  exactly as before, covered by explicit regression tests.
* `HumanInLoopAgent` gained optional config; `context_fields` unchanged.
* `NodeType` gained three ClassVars with documented defaults; the 43 existing
  types are classified through lookup tables rather than 43 edits.
* Every existing workflow YAML still loads, validates and runs.

---

## 17. Remaining specialized nodes, and why

The 43 existing types stay because each represents a genuine capability
boundary, not a prompt variation:

| Kept because | Examples |
| --- | --- |
| Bounded autonomous research | `BoundedDeepResearchAgent`, `ScientificResearchPlannerAgent` |
| Vector retrieval and evidence verification | `RAGAgent`, `ClaimEvidenceVerifier`, `PaperQAEvidenceSynthesizerAgent` |
| Document rendering | `DOCXProposalRenderer`, `PDFProposalRenderer`, `PowerPointProposalSlides` |
| External protocols | `MCPAgent`, `ScientificSkillAgent`, `WebSearchAgent` |
| Domain-specific state machines | `ProposalSubmissionGate`, `ProposalTruthGraphAgent`, `ConceptFreezeAgent` |

`TransformAgent` (an LLM transform) keeps its name and behaviour for the
workflows that use it; `DataTransformAgent` is the deterministic counterpart,
which is a real capability boundary rather than a different prompt.

---

## 18. Live walkthrough

The §44 sequence, driven through the real API end to end.

**1. The workflow validates, zero tokens**

```
valid=True  tokens=0  checks=10  issues=0
```

**2. What "Understand Customer Request" guarantees** (Outputs tab)

```
result.intent            enum      always set
result.equipment.model   string    may be empty
result.customer.company  string    may be empty
confidence               number    may be empty
```

**3. The rule editor's operators come from the field's type**

```
confidence (number)          → equals, not_equals, greater_than, less_than, >=, <=
missing_information (list)   → contains, not_contains, is_empty, is_not_empty, exists
production_stopped (boolean) → is_true, is_false, equals, not_equals
```

**4. Paste the German email and run the simulation**

```
Unsere Dura 15 Pumpe ist ausgefallen.
Die Seriennummer ist 82912.
Unsere Produktion steht still.
```

```
status=completed
path: incoming_request → understand_request → automation_safety
      → route_request → technical_support

Understand Customer Request   [AI inference]
    language = de
    intent = technical_support
    equipment.model = Dura 15, serial_number = 82912

Check Automation Safety       [Deterministic rules]
    ✓ Stopped production is critical
        outputs.understand_request.result.production_stopped is true
        urgency = 'critical'

Route Customer Request        [Deterministic routing]
    ✓ intent equals 'technical_support'

Technical Support             [Deterministic transform]
    title = [critical] Dura 15 — Restore the pump urgently.
```

**5. Why did it route there?**

```
Branch taken: technical_support
Rules that fired: ['Stopped production is critical',
                   'High urgency for a strategic account is critical']
Conclusions: {"human_review": false, "urgency": "critical",
              "automation_allowed": true}
```

**6. Change only the confidence to 0.64 and rerun**

```
status=paused
path: incoming_request → understand_request → automation_safety
      → route_request → human_review
waiting for a person at: ['human_review']

Rules that fired: ['Low confidence needs a person',
                   'Stopped production is critical', …]
Escalation reason: The extraction confidence was below the 0.80
                   threshold for automatic handling.

Branch taken: human_review
    ✓ outputs.automation_safety.decisions.human_review is true
    ✓ outputs.automation_safety.decisions.automation_allowed is false

The reviewer sees 6 labelled panels:
    - Original communication: Exactly as the customer wrote it, untranslated.
    - Extracted information: What the AI understood. Correct anything wrong.
    - Confidence: Below 0.80 the workflow does not act automatically.
    - Missing information: What a colleague would still need.
    - Suggested route: Where this would have gone automatically.
    - Reason for escalation: Which rule sent this request to you.
```

Same workflow. One number changed. The graph routes elsewhere, and every step
explains itself.

None of this required a new NodeType, a backend restart, a YAML edit, or a
database migration.

---

## 19. Building it live

```
 1. Add Input                 drag "Input", declare subject / message
 2. Paste a sample email      into the Input step's sample, or the Simulator
 3. Add AI Task               rename it "Understand Customer Request"
 4. Build the output schema   rows, or "Ask AI to draft this"
 5. Test extraction           Test tab, real call, seconds
 6. Add Decision              rename it "Check Automation Safety"
 7. Add 2–4 rules             field picker + typed operators, or describe them
 8. Add Router                pick the intent field, one click adds every branch
 9. Connect the branches      labels appear on the canvas edges
10. Add Human Review          configure the panels the reviewer sees
11. Run the simulation        watch the path light up
12. Explain every decision    each step says what decided it, and why
```

No YAML editing, no Python, no backend restart, no NodeType subclass, no
migration.
