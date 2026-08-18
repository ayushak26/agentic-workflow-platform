# UI Builder Guide — Reconstructing All 10 Workflows By Hand

Every control named here is real and verified against the live frontend (`ui/src/modes/studio/*.tsx`) — not invented. Palette search terms match `PALETTE_LABELS`/`HIDDEN_FROM_PALETTE` in `NodePalette.tsx`: **Start**, **End**, **Decision**, **Router**, **Human Review**, **Email**, **Integration**, **MCP Tool**, **Transform** (used for both AI and deterministic steps, via its `mode` config), **Join** (`TextAssemblerAgent`'s palette label). Anything else — `SubprocessAgent`, `KnowledgeRetrieval`, `RAGAgent`, `WebSearchAgent`, `MCPAgent`, `PythonSnippetAgent`, `WorkflowFileLoader`, `GraphNormalizer`, and every specialized Proposal Engineering/Research node — has no friendly label; search its exact class name.

## The 12-step pattern, generically

```
1.  Configure Start           — drag Start, set mode (form/chatbot), add fields
2.  Add node                  — drag the next node from the palette
3.  Connect nodes              — draw an edge from the previous node's dot
4.  Configure node              — fill in the Configure tab
5.  Map input                  — Inputs tab, or the field's own picker, for every value the node needs from upstream
6.  Check output                — Test tab, sample input, confirm the fields you'll reference downstream actually appear
7.  Add Decision                — drag Decision, write rules against real upstream fields
8.  Configure routes            — drag Router, mode field/conditions/multi, map route values to real downstream node ids
9.  Configure Join              — drag Join (search "Join"), list every branch's field as a `parts` entry — only after unconditional fan-out, never after a Router
10. Add Human Review            — drag Human Review, write review_panels against real upstream fields
11. Test                        — Inspector's Test tab (single node) or Simulator (whole workflow, one example)
12. Run preflight                — Builder's Validate action; fix by the reported code, same as `scripts/preflight_workflows.py`
```

Below, each workflow gets only the steps that are distinctive — the parts worth actually reading — with a pointer back to this generic pattern for the rest.

---

## W01 — Intelligent Customer Inquiry Resolution

1. **Start**: mode `input_form`. Add the 9 fields listed in the file (`existing_customer` toggle, `company_name`, `contact_name`, `email` format `email`, `country` preset `country`, `inquiry_type` dropdown, `order_reference`, `message` textarea, `attachments`).
2. Drag **`SubprocessAgent`** (search the class name — no friendly label), set `workflow: sp01_multilingual_message_understanding`. In the **Inputs tab**, map `message ← Start.data.message`.
3. Drag a second `SubprocessAgent`, `workflow: sp02_customer_identity_resolution`, map `stated_company_name ← Start.data.company_name`, `order_reference ← Start.data.order_reference`.
4. Drag **Transform**, mode `deterministic`, to consolidate fields for the human-review context summary — but for **Decision's own rule fields**, map directly from the two subprocess nodes' `.result.*` paths, not through this Transform step. (This is the one real gotcha in this whole portfolio: a Decision rule's condition field can't reliably reference a deterministic Transform's dynamic `data.<target>` output — only a node's own declared, typed fields. The Builder's field picker will show you this by simply not offering the Transform's dynamic fields as rule-field options; if you're hand-editing YAML instead, `scripts/preflight_workflows.py` catches it immediately as `UNKNOWN_FIELD_REFERENCE`.)
5. **Decision**: write the 6 rules exactly as in the file, referencing `understand.result.*` and `resolve_identity.result.*`.
6. **Router**, mode `conditions`, single case `NEEDS_REVIEW`, `fallback: PROCEED`.
7. On the `NEEDS_REVIEW` branch: `SubprocessAgent` → `sp04_approval_gate`, then a second **Router** checking `ambiguity_review.result.decision equals approve`, branching `APPROVED`/`STOPPED` (fallback `STOPPED`).
8. Both `APPROVED` and `PROCEED` call the **same** `SubprocessAgent` (`w01sub_route_and_notify`) — as two separate node instances, one per branch. Do **not** try to route both branches into one shared subprocess-call node; that's exactly the reconvergence hazard this portfolio's design note warns about.
9. Each terminal branch gets its own **End**, `mode: workflow_result`.

## W02 — New Customer RFQ Intake and Qualification

The distinctive part is the **Start** node's field variety — this is the one to open in the Builder just to see the input-type range: dropdown (`product_category`), multi-select (`required_certifications`), date (`required_delivery_date`), a repeating table (`line_items`, `display: table`), a toggle (`needs_custom_engineering`), and a field that only appears when the toggle is on (`engineering_requirements`, `visible_when`). Build the conditional field last: add the toggle first, then add `engineering_requirements` and use its **Configure tab's condition builder** to set `visible_when: needs_custom_engineering equals true` — the same rule-condition UI Decision/Router use.

After Start: drag **Transform** (mode `ai`) for `extract_unstated_requirements`, then fan out with three plain connections to **`KnowledgeRetrieval`**, **`RAGAgent`**, and **Router**'s eventual predecessor `WebSearchAgent` — draw all three edges from the same source node; the Builder draws a normal edge per connection, there's nothing special to configure for the fan-out itself. **Decision** → **Router** (mode `field`) → three separate branches, each ending on its own **Transform** (deterministic) + **End**, except `TECHNICAL` which gets a **Human Review** first.

## W03 — Existing Customer Technical Service Case

**Start**: mode `chatbot`. Note there are no form fields to add in this mode — just `chatbot_name`, `welcome_message`, and `suggested_questions`. Downstream, address the customer's text as `Start.message`, not `Start.data.message` — chatbot mode's output shape is different from form mode's, and the Builder's field picker for a chatbot Start only offers `message`/`attachments`.

For the two **End** nodes that reply in the chat, set `mode: chat_response` (not `workflow_result`) and fill `chat_message` — this is the natural End mode for anything downstream of a chatbot Start.

## W04 — Order Status and Delivery Exception Management

This is the cleanest real example of the **safe** parallel-then-join shape in the whole portfolio. After the understanding subprocess:
1. Draw **three separate edges** from `understand` to `get_order`, `get_shipment`, `get_fulfilment_status` — do this by connecting each one individually; there's no "fan out" button, you just draw three edges from the same source dot.
2. `get_order` additionally connects on to `get_inventory` (since inventory needs the order's pump model first).
3. Drag **Join** (search "Join"). Connect `get_shipment`, `get_fulfilment_status`, and `get_inventory` all into it. In its Configure tab, add one `parts` entry per branch — each is just a template string, built with the field picker same as any other text field.
4. **Decision** reads the *original* MCP nodes' typed fields directly (`get_order.first.fulfilment_status`, etc.) — not the Join's own `text` output, which is just the assembled string for a human to read, not a structured field a rule can branch on.

## W05 — Quote and Discount Approval

The Multi-Route case worth studying directly in the Builder: open the **Router** node, set `mode: conditions`, and toggle **`selection: multi`** in the RouterEditor. Add two cases (`SALES_MANAGER`, `FINANCE`) with independent `when` conditions — the Builder's own UI note here (visible right in the RouterEditor) tells you to collect each selected branch's result as its own output rather than trying to reconverge them with a Join, which is exactly what this workflow does: three fully independent terminal branches (`AUTO`, `SALES_MANAGER`, `FINANCE`), each with its own `create_opportunity` call and its own End.

## W06 — Purchase Request and Supplier Approval

The distinctive point: `manager_review` (Human Review) is connected with a **single plain edge** straight after the policy checks — not through a Router — because manager approval isn't conditional here; it's always required. Only *after* that mandatory step does a **Router** (`selection: multi`) decide whether Procurement and/or Finance also need to see it. Don't reach for a Router just because a step involves a human — only use one when the *path itself* is actually conditional.

## W07 — Invoice Exception Verification

1. **Start** with a file field (`invoice_file`), then **`WorkflowFileLoader`**, then **Transform** (mode `ai`) to extract the four invoice fields.
2. Fan out (three plain edges) to `find_invoice_record`, `find_order_record`, `find_customer_record`.
3. **Join** combines all three into a readable summary; separately, drag **`PythonSnippetAgent`** (search the class name) fed by `find_invoice_record` alone, for the exact numeric comparison — write the comparison in its `code` field exactly as a short Python snippet, not a prompt. This is the node to reach for whenever a comparison needs to be *exact*, not "close enough" per a model's judgment.
4. **Decision** reads both the Join's contributing nodes directly and the PythonSnippetAgent's `result.amounts_match` — **Router** (conditions) → `AUTO_PROCESS` or `EXCEPTION` → Human Review only on the exception path.

## W08 — Employee Onboarding Orchestration

Five branches fan out from Start with five separate edges: two are `SubprocessAgent` nodes (`w08sub_hr_setup`, `w08sub_it_account` — search the class name, map each subprocess's declared inputs individually in its Inputs tab), and three are inline (`Transform` ×2, `Email`). All five converge on one **Join** — safe, because all five are unconditional. **Decision** reads `it_account.result.requires_security_review` directly (a subprocess's own declared output field, not a dynamic Transform field) — this is exactly the same "read from the typed field, not the dynamic one" pattern as W01.

## W09 — Internal IT Helpdesk and Access Request

The **`MCPAgent`** node (search the class name) is the one to look at closely: its Configure tab has an `allowed_tools` list — add exactly `query_readonly` and nothing else. This is what makes it safe to use here despite having no author-authored policy gate the way MCP Tool does: the model can only ever call the one read-only tool you've explicitly listed, never a write tool, regardless of what it decides to do.

## W10 — Evidence-Grounded Business Proposal Document

The largest workflow (29 nodes) — build it in the same six stages the file's own comments follow:
1. **Structure**: Start → `WorkflowFileLoader` → `GraphNormalizer` (search the class name; no config fields beyond `concept_note`/`call_facts`/`model` — it reads/writes the shared proposal graph behind the scenes, not through template fields).
2. **Research**: `ScientificResearchPlannerAgent` → three parallel branches (`ScholarlyCandidateDiscoveryAgent`, `WebSearchAgent`, `BoundedDeepResearchAgent`) — note `ScholarlyCandidateDiscoveryAgent` has **no** `research_briefs` config field (a real gap between what you might expect from its name and its actual Configure tab — it reads unresolved claims from the graph directly instead).
3. **Evidence**: `bounded_research` → `ResearchSourceAcquirer` → `ProposalEvidenceFactoryAgent` → **Human Review** (`evidence_review`) → `ProposalTruthGraphAgent`, whose `evidence_approval_decision` field maps directly from `evidence_review.decision`.
4. **Concept & Drafting**: `ConceptAlternativesAgent` → **Human Review** → `ConceptFreezeAgent` → `MethodologyEngineeringAgent` → two parallel `Transform` (`mode: ai`) drafting steps → **Join**.
5. **Evaluation & Gate**: three parallel checks (`ConsistencyChecker`, `CallCoverageMatrixAgent`, `HorizonEvaluationAgent`) all feeding `ProposalSubmissionGate` — when configuring `HorizonEvaluationAgent`, pick `evaluator_models` from the model dropdown and make sure **neither** matches `generator_model`; the node's own validator rejects it otherwise (visible immediately in the Test tab, or as `MODEL_NOT_IN_CATALOG`/a validation error in preflight if you pick a model outside the approved catalog).
6. **Submit & Render**: Router on `submission_gate.status` → Human Review (`final_approval`) → three parallel renderers (`HorizonDOCXProposalRenderer`, `HorizonHTMLProposalRenderer`, `PowerPointProposalSlides`) → one shared **End**. Three plain edges converging on one End is safe here because Human Review's own fan-out behavior (approve/edit → *every* outgoing edge fires) is exactly the unconditional case, not the router-branch case — same safety rule, different source.

Throughout, remember: `ProposalEvidenceFactoryAgent`'s and `ConceptAlternativesAgent`'s output fields are **not** nested under a `.result` wrapper the way a `SubprocessAgent`'s or `DecisionAgent`'s are — the field picker will show you their real top-level fields (`verified_claims`, `citation_registry`, `alternatives`, `recommended_concept_id`, …) directly; don't type `.result.` in front of them by habit from the other node types.

---

## A note on the subprocesses themselves

Every `SPxx`/`w0Nsub_` file is built exactly like any other workflow **except it has no Start node** — its `entry` is whatever its first real processing node is, and its declared top-level `inputs:` block is what a parent workflow's `SubprocessAgent.inputs` mapping fills in. If you're building one in the Builder, there's no separate "subprocess mode" toggle — you're just building a workflow that happens not to begin with Start, saved under a name a `SubprocessAgent` node elsewhere can reference by its filename (without the `.yaml` extension).
