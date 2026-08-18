# 10 Business Process Workflows — Master Guide

Ten real business processes, built from this platform's actual live node registry — every field, tool name, and MCP connection verified against source (`app/nodes/*.py`, `app/mcp/*/tools.py` + `handlers.py`) before use, not invented. All 18 workflow files (10 main + 5 reusable subprocesses + 3 workflow-specific subprocesses) pass `scripts/preflight_workflows.py` with 0 errors, 0 warnings — see `VALIDATION_REPORT.md`.

## The ten workflows

| # | Workflow | File | Department | Main Business Value | Key Capabilities |
|---|---|---|---|---|---|
| 01 | Intelligent Customer Inquiry Resolution | `w01_intelligent_customer_inquiry_resolution.yaml` | Customer Service / Sales / Engineering / Supply Chain | A multilingual, possibly multi-department inquiry gets understood, identity-resolved against real records, and routed — escalating only when genuinely ambiguous | Multi-Route, subprocess reuse (×2), MCP Tool, Decision, Human Review, Email |
| 02 | New Customer RFQ Intake and Qualification | `w02_new_customer_rfq_intake.yaml` | Sales / Sales Engineering | A technical RFQ is qualified deterministically as standard, technical, or missing information — before it ever reaches a salesperson's queue | Rich Start inputs (dropdown, multi-select, number+unit, date, repeating table, conditional field), RAG, Knowledge Retrieval, Web Search |
| 03 | Existing Customer Technical Service Case | `w03_technical_service_case.yaml` | Customer Service / Engineering | An equipment problem reported via chat is identified against real installed-unit records and answered from the actual service manual | Chatbot Start + `chat_response` End, subprocess reuse (×2), RAG, MCP Tool |
| 04 | Order Status and Delivery Exception Management | `w04_order_status_delivery_exceptions.yaml` | Customer Service / Supply Chain | Four independent order-status checks run in parallel and join safely into one delivery assessment | Unconditional parallel fan-out, Join (`TextAssemblerAgent`), MCP Tool ×4, Email |
| 05 | Quote and Discount Approval | `w05_quote_discount_approval.yaml` | Sales / Finance | A quote needing both a discount exception and a large-deal sign-off gets both, on independent tracks | Multi-Route (genuinely simultaneous approvals), MCP Tool, Human Review ×2 |
| 06 | Purchase Request and Supplier Approval | `w06_purchase_request_supplier_approval.yaml` | Procurement / Finance | Mandatory manager approval plus conditional Procurement/Finance escalation, either or both | Mandatory step wired directly (not a router), Multi-Route escalation, RAG/Knowledge Retrieval for policy |
| 07 | Invoice Exception Verification | `w07_invoice_exception_verification.yaml` | Finance | An uploaded invoice is verified against the system's own records with an exact, deterministic amount comparison | File upload + extraction, parallel MCP checks + Join, Python exact comparison, Decision, Human Review |
| 08 | Employee Onboarding Orchestration | `w08_employee_onboarding_orchestration.yaml` | HR / IT / Facilities | Five onboarding workstreams — two of them reusable subprocesses — run in parallel and join into one readiness decision | Subprocess reuse (×2), unconditional parallel fan-out, Join |
| 09 | Internal IT Helpdesk and Access Request | `w09_it_helpdesk_access_request.yaml` | IT | Most requests answer from documentation alone; access requests get a genuinely bounded, tool-restricted investigation | Chatbot Start, subprocess reuse, `MCPAgent` (bounded to one read-only tool) |
| 10 | Evidence-Grounded Business Proposal Document | `w10_evidence_grounded_proposal.yaml` | Research / Consulting | A call and concept note become a submission-gated proposal — every specialized research/evidence/proposal/rendering family working together in real business order | 29 nodes, 3 independent discovery sources, 2 mandatory human approval gates, parallel drafting joined safely, dual cross-provider evaluation, 3 rendered outputs |

## Reusable subprocesses

| Subprocess | File | Used by | What it does |
|---|---|---|---|
| SP01 — Multilingual Message Understanding | `sp01_multilingual_message_understanding.yaml` | W01, W03, W04 | Detects language, translates, extracts 6 facts each tagged KNOWN/INFERRED/MISSING/AMBIGUOUS |
| SP02 — Customer Identity Resolution | `sp02_customer_identity_resolution.yaml` | W01, W03 | Resolves stated identity against Finance & SCM records, preserving both the stated and resolved name — never overwrites |
| SP03 — Internal Knowledge Answer | `sp03_internal_knowledge_answer.yaml` | W03, W09 | Answers from internal knowledge, with an explicit HAS_INTERNAL_EVIDENCE / NO_INTERNAL_EVIDENCE status |
| SP04 — Approval Gate | `sp04_approval_gate.yaml` | W01 | A generic approve/reject/edit human gate for a pre-formatted context summary |
| SP05 — Response Preparation | `sp05_response_preparation.yaml` | W01 (via its own subprocess) | Drafts a customer response, reviews it when required, then sends — two fully independent terminal branches, never reconverged |

Two more subprocesses are workflow-specific rather than general-purpose (named `w01sub_`/`w08sub_` rather than `sp0N_` to make that distinction visible at a glance):

| Subprocess | File | Used by | Why it's a subprocess and not inline |
|---|---|---|---|
| W01 Subprocess — Route To Departments And Notify | `w01sub_route_and_notify.yaml` | W01, called from two different mutually-exclusive parent branches | Called identically from both W01's "after human review" and "no review needed" paths — a subprocess avoids duplicating that whole Multi-Route + case-creation + notification subgraph twice in the parent |
| HR Setup | `w08sub_hr_setup.yaml` | W08 | A genuinely reusable "set up an HR record" capability, callable from any future onboarding-shaped workflow |
| IT Account Provisioning | `w08sub_it_account.yaml` | W08 | A genuinely reusable "provision an account" capability — could equally be called from W09's access-request flow |

## The design principle this portfolio follows

```
UNSTRUCTURED INFORMATION  →  AI TASK ("what does this mean?")  →  STRUCTURED INFORMATION
        ↓
MCP / RAG / BUSINESS RECORDS ("what facts already exist?")
        ↓
DECISION ("what known business rules apply?")
        ↓
ROUTER / MULTI-ROUTE ("who needs to act?")
        ↓
WORK  →  HUMAN REVIEW ("does a person need to decide?")  →  ACTION / OUTPUT
```

Every Decision node in this portfolio evaluates a fixed threshold, an enum equality, or a boolean flag — never "does this feel urgent" left to a model. Every AI step (`TransformAgent`, `mode: ai`) extracts or drafts — it never decides a business rule. This split is checkable directly in the YAML: grep any `DecisionAgent`/`RouterAgent` block in this portfolio and every condition's `value` is a literal, never a prompt.

## The one rule that shaped every branching decision in this portfolio

**Never let two mutually-exclusive paths reconverge on a shared node.** This applies to a Multi-Route router's unselected branches, a single-select router's branches, and even two paths that reach the same *kind* of step through different routes. See `NODE_COVERAGE_MATRIX.md`'s note and the header comments in `w01sub_route_and_notify.yaml` / `sp05_response_preparation.yaml` for the two real, worked cases where this was caught and fixed before it shipped as a bug. The fix is always the same: give each mutually-exclusive branch its own terminal node instead of sharing one. Small duplication, fully safe.

## Where to go next

- **`NODE_COVERAGE_MATRIX.md`** — every registered node type, where it's used, and a specific reason for every one that isn't
- **`INTEGRATION_COVERAGE.md`** — the three real connected business systems, their verified tool catalogs, and one real catalog/handler drift to avoid
- **`10_WORKFLOWS_UI_BUILDER_GUIDE.md`** — how to reconstruct each workflow by hand in the Builder
- **`VALIDATION_REPORT.md`** — the full preflight results and final numbers
