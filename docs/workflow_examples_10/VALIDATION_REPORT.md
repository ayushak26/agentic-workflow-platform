# Validation Report

Every number below is either the direct output of `scripts/preflight_workflows.py`, a `grep -l` count against the actual 10 workflow files, or the output of `tests/test_workflow_examples_10_coverage.py` — not an estimate.

## Headline numbers

```
Business workflows:                          10
Reusable subprocesses (SPxx, general-purpose): 5
Workflow-specific subprocesses (w0Nsub_):       3
Registered node types (app/nodes/registry.py): 57
Demo-eligible node types (57 - 3 hidden):      54
Distinct node types used by the portfolio:     34
Portfolio coverage:                            34 / 54 = 63.0%
  (+ IntegrationAgent demonstrated elsewhere this session, not in the 10:
     35 / 54 = 64.8%)

Preflight: 18 / 18 files PASS, 0 errors, 0 warnings, 0 tokens
Node-coverage regression test: 3 / 3 PASSED (tests/test_workflow_examples_10_coverage.py)
```

## Preflight — every file, individually

```
PASS workflows/sp01_multilingual_message_understanding.yaml         —  2 nodes
PASS workflows/sp02_customer_identity_resolution.yaml                —  5 nodes
PASS workflows/sp03_internal_knowledge_answer.yaml                   —  5 nodes
PASS workflows/sp04_approval_gate.yaml                               —  2 nodes
PASS workflows/sp05_response_preparation.yaml                        —  7 nodes
PASS workflows/w01sub_route_and_notify.yaml                          — 14 nodes
PASS workflows/w08sub_hr_setup.yaml                                  —  2 nodes
PASS workflows/w08sub_it_account.yaml                                —  3 nodes
PASS workflows/w01_intelligent_customer_inquiry_resolution.yaml      — 13 nodes
PASS workflows/w02_new_customer_rfq_intake.yaml                      — 14 nodes
PASS workflows/w03_technical_service_case.yaml                       — 12 nodes
PASS workflows/w04_order_status_delivery_exceptions.yaml             — 14 nodes
PASS workflows/w05_quote_discount_approval.yaml                      — 13 nodes
PASS workflows/w06_purchase_request_supplier_approval.yaml           — 11 nodes
PASS workflows/w07_invoice_exception_verification.yaml               — 13 nodes
PASS workflows/w08_employee_onboarding_orchestration.yaml            — 12 nodes
PASS workflows/w09_it_helpdesk_access_request.yaml                   — 12 nodes
PASS workflows/w10_evidence_grounded_proposal.yaml                   — 29 nodes
```

18/18 PASS. Every failure encountered *while building* was fixed before moving on — none were suppressed or worked around; see "Real errors caught and fixed" below for exactly what preflight actually caught.

## Capability coverage across the 10 workflows (counted by `grep -l`, not estimated)

```
Form Start workflows:                8 / 10
Chatbot Start workflows:              2 / 10
Decision workflows:                   9 / 10
Router workflows:                    10 / 10
Multi-Route workflows:                2 / 10   (W05, W06 — genuinely simultaneous approvals)
Join workflows:                       4 / 10   (W04, W07, W08, W10 — always unconditional fan-out first)
Human Review workflows:               9 / 10
RAG workflows:                        3 / 10   (W02, W03, W06)
Knowledge Retrieval workflows:        2 / 10   (W02, W06)
Web Search workflows:                 2 / 10   (W02, W10)
MCP Tool workflows (direct):          4 / 10   (W03, W04, W05, W07 — W01 uses it via its subprocess)
MCP Agent workflows:                  1 / 10   (W09, bounded to one read-only tool)
MySQL / business_records workflows:   2 / 10   (W05, W09 — direct; W01 via w01sub)
Email integration workflows:          2 / 10   (W04, W08 — direct; W01 via SP05)
Subprocess workflows:                 5 / 10   (W01, W03, W04, W08, W09)
File-upload workflows:                2 / 10   (W07, W10)
Research/evidence workflows:          1 / 10   (W10 — by design; this family is Horizon-proposal-specific)
Rendering/export workflows:           1 / 10   (W10)
Python-exact-comparison workflows:    1 / 10   (W07 — SP02 also uses it, as a subprocess)
```

These numbers are lower than "every capability in every workflow" by design — §19 and §30 of the brief this portfolio was built against explicitly warn against forcing coverage where it has no business reason. Every "not in every workflow" figure above is deliberate breadth across the portfolio, not partial completion of any one workflow.

## Real errors caught and fixed while building — not simulated

These are the actual `preflight_workflows.py` failures hit during construction, kept here so the next person extending this portfolio doesn't re-discover them the slow way:

1. **`UNKNOWN_FIELD_REFERENCE` on a `DecisionAgent` rule reading a deterministic `TransformAgent`'s `data.<target>` field** (W01, `decide_routing`). A Decision/Router rule's condition field must be a well-typed, declared field — a subprocess's `.result.<path>`, a node's own first-class output field (`found`, `count`), or an AI step's declared `output_fields` — never a deterministic Transform's dynamically-named `data.<target>` output, which preflight cannot see into. Fixed by reading straight from the two subprocess calls' `.result.*` instead of through an intermediate consolidation step.
2. **`NODE_CONFIG_INVALID` on `EmailAgent.to`** (the original interview-guide demo, and again in this portfolio's design phase) — `to` needs a list of `{email, name}` objects, not bare strings.
3. **`ScholarlyCandidateDiscoveryAgent` has no `research_briefs` config field** (W10) — despite `BoundedDeepResearchAgent` (used two lines away in the same fan-out) taking exactly that field. It reads unresolved claims from the shared proposal graph directly instead. Caught immediately by `UNKNOWN_NODE_CONFIG_FIELD`.
4. **`MODEL_NOT_IN_CATALOG` on `kimi-k3`** (W10, `horizon_evaluation.evaluator_models`) — the real approved catalog is `claude-opus-5, claude-sonnet-4-5, claude-haiku-4-5, claude-fable-5, gpt-5.6-sol, gpt-5, gpt-5-mini, gpt-5.6-terra, gpt-5.6-luna, gpt-4o-mini, local-kimi-k3, local-glm-5`, or an `openrouter/<vendor>/<model>` id — preflight's own error message listed it exactly, no guessing needed.
5. **`ProposalEvidenceFactoryAgent` and `ConceptAlternativesAgent` outputs are not nested under `.result`** (W10) — unlike `SubprocessAgent`/`DecisionAgent`, their declared output fields (`verified_claims`, `citation_registry`, `alternatives`, `recommended_concept_id`, …) sit at the top level. Caught by `TEMPLATE_UNKNOWN_OUTPUT_FIELD`, which listed the real field names directly.
6. **A structural bug caught before it was ever written to preflight at all**: `find_crm_account` and the department `Router` were about to be built as unconnected parallel siblings under one fan-out node in `w01sub_route_and_notify.yaml`, which would have made every department branch's reference to `find_crm_account.first.account_id` structurally invalid (no guaranteed upstream path). Restructured to a linear chain (`find_crm_account → department_router → branches`) before ever running preflight on it.
7. **The Multi-Route→Join hazard itself**, caught mid-design in `sp05_response_preparation.yaml` before it was written: a "skip review" branch and a "went through review" branch were about to share one `EmailAgent` node. Fixed by giving each mutually-exclusive branch its own terminal node — see `README.md`'s design-principle section and the header comments in `w01sub_route_and_notify.yaml`/`sp05_response_preparation.yaml`.

## Real, verified platform facts this portfolio is grounded in (not invented)

- Three distinct MCP-connected business systems, with their exact tool catalogs verified against `app/mcp/{dynamics,d365_finance,business_records}/tools.py` **and** cross-checked against each connector's own `HANDLERS` dispatch dict — not assumed from the catalog alone. One real catalog/handler drift was found and avoided (`dynamics365`'s quotation/shipment/service-case tools — see `INTEGRATION_COVERAGE.md`).
- The real MySQL schema behind them (`app/mcp/business_records/schema.sql`, 23 tables) and real seed fixture values (`app/mcp/d365_finance/fixtures.json`) — every enum value a `DecisionAgent` rule compares against in W04 (`MATERIAL_SHORTAGE`, `QUALITY_HOLD`, `PRODUCTION_DELAY`, `NOT_AVAILABLE`) is a real value from the actual seed data, not guessed.
- `FieldSpec`'s real nested-object support (`app/runtime/field_schema.py`), confirmed before relying on it for the KNOWN/INFERRED/MISSING/AMBIGUOUS extraction contract used throughout.
- The real, separate Finance & SCM vs. CRM account-id spaces, and the two-lookup pattern (`find_customer` then `find_account`) this makes necessary before filing a CRM case from a Finance & SCM-resolved identity.

## What was not, and could not honestly be, tested

No workflow in this portfolio was executed end-to-end against a live model, a live MCP connection, or a live database in this session. "PASS" means `scripts/preflight_workflows.py` proved the graph, every node's config, every template reference, every Decision/Router field, and (via `compile_graph=True` where used) the compiled LangGraph itself are structurally valid — deterministically, with zero LLM calls and zero tokens spent. It does not mean any workflow has been run with real data. Treat every business outcome described in these files as the *intended* behavior given the real schemas and tool contracts referenced, not as an observed one.

## Excluded nodes

See `NODE_COVERAGE_MATRIX.md`'s exclusion list — 20 demo-eligible node types, each with a specific, individually-stated business or technical reason, verified by `tests/test_workflow_examples_10_coverage.py` to exactly match what the portfolio actually leaves uncovered (no drift in either direction).
