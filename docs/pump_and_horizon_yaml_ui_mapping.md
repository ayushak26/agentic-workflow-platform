# Pump & Horizon Workflows — YAML ↔ Builder UI Mapping

Covers the 6 workflows: `pump_routing_level_1`, `pump_routing_level_2`,
`pump_manufacturer_case_routing`, `horizon_partb_evidence`,
`horizon_partb_drafts`, `horizon_partb_drafts_to_docx`.

Built from (1) a full read of all 6 YAML files plus
`workflows/pipelines/horizon_partb.pipeline.yaml`, (2) the Builder frontend
source (`ui/src/modes/studio/**`) and the live `GET /api/node-types` schema,
and (3) live verification in the running Builder — each of the 6 workflows
was opened via `/builder/<slug>` and a representative node of every distinct
type was selected and its Configure/Advanced tabs inspected directly in the
DOM to confirm what's a real form field vs. a raw-JSON textarea.

> **Update (2026-08-16, after initial publication):** `TransformAgent` was
> redesigned while this document was being written, and all TransformAgent
> nodes across **all 6 workflows** were converted from the old
> `prompt_template`/`system_prompt`/`output_schema` shape to a new
> `instructions`/`input_fields`/`output_fields` shape — the same
> Inputs → Instructions → Outputs editor pattern `AITaskAgent` already uses,
> via a new `PromptTemplateConfig.tsx` editor. This is a real, substantial
> change, not a cosmetic one: TransformAgent's Configure tab went from
> "generic form with one JSON textarea" to "fully structured, no JSON
> textarea" for every migrated node. B.1, B.2, Part C and Part D below have
> been corrected to match the live app; anywhere still describing the old
> shape has been struck through or annotated. If you're reading a cached
> copy of this document, treat any remaining `output_schema:JSON` claim
> about `TransformAgent` as stale.

**How to read "Editable in UI":**
- **Structured** — a real form control (text box, checkbox, number input,
  dropdown, or a purpose-built editor like the rule builder). Changing it in
  the Builder and changing it in the YAML are equivalent.
- **Raw JSON (Configure tab)** — the Builder *does* let you edit it (a
  monospace textarea holding the field's JSON value, with the hint "Edit as
  JSON"), but there's no structured form — you're hand-typing YAML-shaped
  JSON either way, just inside the Builder instead of your editor.
- **YAML-only** — no control anywhere in the Builder touches this. The only
  way to change it is to hand-edit the YAML file (or re-import/re-export it).
- **Computed / derived** — the Builder works this out from the graph itself;
  there's nothing to "set," so it's neither a YAML-editing nor a UI-editing
  task most of the time.

---

## PART A — Workflow-level (top-level YAML keys) → UI

All 6 files share the same top-level key set: `name, description, version,
use_case, library, inputs, entry, exit, output, nodes, edges` (only
`pump_manufacturer_case_routing.yaml` additionally has `x_outcome_fragments`).

| YAML key | Where it shows up in the UI | Editable in UI? | Notes |
|---|---|---|---|
| `name` | Builder header ("Pump Customer Routing — Level 1 · Simple"), Library/My Work cards | **Structured**, indirectly | Not a text field you type into directly — it's set by the workflow's filename/slug at creation ("Save As" dialog) plus whatever the YAML's `name:` says. Renaming later means editing YAML `name:` or using "Save As" again. |
| `description` | **Nowhere in the Builder UI.** Not shown in Library cards, My Work, or any Inspector tab. | **YAML-only** | Confirmed by grep across the whole frontend — zero references to `workflow.description`. This is pure documentation/prompt-adjacent metadata; change it only in the YAML. |
| `version` | Builder toolbar, read-only ("workflow v1.0") | **YAML-only** (display is read-only) | Shown but not editable from that label. There's a separate "Versions" button/panel for the Builder's own version history — don't confuse the two; that panel is about Builder-tracked snapshots, not this YAML field. |
| `use_case` | **Nowhere in the Builder UI.** | **YAML-only** | No frontend reference found. Purely a backend/categorization tag. |
| `library.*` (title, summary, purpose[], suitable_for[], not_suitable_for[], outputs[], input_types[], human_reviews{}, visibility_status) | **Library page only** (`Library.tsx`) — `library.outputs` populates the filter-tag pills on a workflow's card; `library.visibility_status === 'approved'` is literally what gates a workflow into the "Ready" state shown in Library/My Work. | **YAML-only** (read, never written, by the Builder) | There is no editor anywhere for this block — no add/edit button, no form. If you want a workflow to show different tags or flip to "Ready," you edit this block by hand in the YAML. This is the single most consequential YAML-only block for day-to-day workflow curation. |
| `inputs` | Toolbar **"Inputs"** button → **Workflow Inputs** panel | **Structured** | Fully editable: add/remove an input, rename it, set its type (Text/JSON/File/…), required flag, description. This is the one top-level metadata block with a real UI. |
| `entry` | Implicit — the node with no incoming edge | **Computed / derived** | Not a field you set. The Builder recalculates it from the graph whenever nodes/edges change (see `Builder.tsx`'s node-removal/rename handlers rewriting `entry`). If a workflow legitimately needs a different entry node, you change it by rewiring which node has zero incoming edges, not by typing an id anywhere. |
| `exit` | Implicit — the node(s) with no outgoing edge | **Computed / derived** | Same as `entry`: a list, recalculated from graph topology, not manually typed. Note in the Horizon files this can diverge from `output.nodes` (see Part D). |
| `output.include_input` / `output.nodes[].flatten` | **Not found in the Builder UI.** No Export/Output-mapping panel references this shape. | **YAML-only** | Every one of the 6 files has this and every entry is `flatten: false` — worth double-checking by hand whenever you add/remove a node from a workflow's real output set, since the Builder won't do it for you. |
| `x_outcome_fragments` (manufacturer_case_routing only) | **Not found in the Builder UI anywhere** (grepped the whole frontend — zero references). | **YAML-only** | Purely a runtime/compiler concept for shaping the final structured output. If you add a new "outcome fragment," you're writing it by hand. |
| `nodes` | Canvas + node palette ("+ Add") + Inspector | **Structured** | Add/remove/select nodes on canvas; each node's own `config`/`experience` is covered in Part B/C below. |
| `edges` (plain `{from,to}` or fan-out `{from,to:[...]}`) | Canvas — drag from a node's right (source) handle to another node's left (target) handle | **Structured** | Straightforward drag-to-connect. Deleting: click the edge, delete. |
| `edges` (router branch shape: `{from,condition,branches:{NAME: node_id}}`) | **Router node's own Configure tab** — NOT drawn by dragging on canvas | **Structured, but indirect** | This is the one edge shape you don't create by dragging a connector. It's generated when you fill in a Router's "Route using" field / branches / "Otherwise, send to" fallback in Configure — the Builder writes the corresponding `condition`/`branches` edge for you. If you hand-edit a branch target in YAML without going through the Router's Configure tab, the canvas will still render it as a connection, but you've bypassed the normal authoring path. |
| per-node `experience` block (`display_name, purpose, contribution, expected_output, failure_message`, sometimes `success_condition`) | Node's **Advanced** tab → "Guided Run Identity" section (`display_name`→"Business step name", `purpose`, `contribution`, `expected_output`, `failure_message` each get their own field; also `visibility` and "Business stage") | **Structured** | Fully editable per node. **Important:** all 66 nodes across the 3 Horizon files omit `experience` entirely — if you want Guided Run / Business View to say anything sensible for those workflows, this is 66 nodes of Advanced-tab work with zero existing examples to copy from in those files (the 59 pump nodes all have it filled in already, so copy the *shape* from there). |

---

## PART B — Node type → UI reference (one row per distinct type)

33 distinct node types appear across the 6 workflows. **7 node types now
get a hand-built Configure editor** (6 unconditional, plus `TransformAgent`
conditionally — see B.1); every other type falls back to a generic,
schema-driven form (`SchemaForm.tsx`) that auto-generates one control per
config field based on its JSON-Schema type:

- string → text box (or a 6-row textarea for known prompt/description-shaped
  field names)
- enum → dropdown
- boolean → checkbox
- number/integer → number input
- array-of-strings → one-value-per-line textarea
- **anything else (nested object, array of objects, free-form dict) → a raw
  JSON textarea** with the literal hint *"Edit as JSON. Phase 11 may add a
  typed editor."*

That last bullet is the important one: for the 26 "generic" types below,
any config field that's a nested object/array is JSON-only inside the
Builder too — you're not saving YAML-editing effort by using the UI for
those specific fields, only for the plain scalar ones alongside them.

### B.1 — The 7 node types with a custom Configure editor

| Node type | Custom editor | What's structured | What's still JSON/indirect |
|---|---|---|---|
| **RouterAgent** | `RouterEditor.tsx` | Everything — mode switch ("On a field value" / "On conditions"), `route_field` picker, branch list with "+ Add branch", per-branch value + target, "Otherwise, send to" fallback. Generic schema would call `branches`/`cases`/`rules` all JSON; the live UI has **zero JSON textareas** for either mode (verified live on 6 different Router instances across all 3 pump files + 2 Horizon files). | The router-branch **edges** are a side effect of this editor, not separately drawn (see Part A). Two config *shapes* exist under one type — `mode: field` (branches map) vs `mode: conditions` (cases list) — pick the mode first, the rest of the form changes shape. A legacy `mode: rule`/`llm` is also recognized but shown as a deprecation banner steering you to the two modes above. |
| **DecisionAgent** | inline `DecisionConfig` + `RuleBuilder.tsx` + `ConditionGroupEditor.tsx` | Everything — one rule card per rule with name, "always apply" checkbox, nested AND/OR/NOT condition groups (field dropdown sourced from the real upstream contract, operator dropdown scoped to that field's type, value input), and `then[]` actions (field/operation/value). Plus a "Starting values" (`defaults`) editor. **Zero JSON textareas**, verified live on `department_decision`, `business_context`, all 4 Decision nodes in `pump_manufacturer_case_routing`. | Nothing — this is the most fully-structured editor in the app. (There is no UI feature literally called "Business Facts" — the closest concept is this rule/defaults editor; see the note under `business_facts` in Part C.) The condition row's layout shifted slightly (the "×" remove button moved under the field picker, the value input now stretches full-width) in a small, unrelated UX pass — same fields, same behaviour, just rearranged. |
| **DataTransformAgent** | `DataTransformConfig.tsx` | Per-operation structured fields for every operation kind used in these workflows (`copy, constant, format, object, count, boolean, select`, etc.) — even the `object` operation's nested key/value entries get their own `ObjectEntriesField` widget, not JSON. `omit_empty` is a checkbox. | Live-verified: 1 residual JSON textarea still shows up per node in some cases (e.g. `sales_case`, `multi_intent_case`) — likely an edge-case operation shape the structured editor doesn't cover; check the specific operation before assuming it's fully structured. |
| **MCPToolAgent** | `MCPToolConfig.tsx` | Server dropdown → tool dropdown (both discovered live from the connected MCP server) → the tool's own `arguments` get a **dynamically generated structured form** driven by that specific tool's input schema (verified live on `find_customer`: "customer name*"/"limit" appeared as typed fields, not JSON). `fail_on_error` and `allow_unattended_write` are checkboxes. | `timeout_seconds` / `max_read_retries` weren't observed as visible Configure fields in the instances checked — if you need to change those, check Advanced first, otherwise they may currently only be reachable via YAML. |
| **TransformAgent** — *new-style nodes only* | `PromptTemplateConfig.tsx` (new) | **Inputs** section: one row per named variable (name / type dropdown: Text, Number, Yes-no, Date / description) via "+ Add Input" — the Builder auto-generates the `{{inputs.<name>}}` reference and, on a brand-new variable, auto-creates a matching top-level workflow input too, so you never hand-type template syntax. **Instructions**: a plain textarea (replaces the old raw `prompt_template` with embedded `{{...}}`), plus a new "✨ Draft Instructions" button (calls the new `POST /api/node-types/draft-instructions` endpoint) that drafts or rewrites the instructions from the configured Inputs/Outputs — the same drafting-assistant pattern AI Task's "✨ Draft Prompt" already uses. **Outputs**: reuses the exact same `SchemaBuilder` component AI Task's Configure tab uses (type dropdown per field, Required checkbox, reorder arrows, "Show generated schema"). **Zero JSON textareas** for a new-style node — live-verified on the migrated `understand_message` in all 3 pump files. | Which editor a `TransformAgent` node gets is **conditional, not fixed by type**: a node is "new-style" only once its `instructions` field is non-empty (`isLegacyTransform()` in `ConfigureTab.tsx`, mirroring the backend's `is_new_style()` in `app/nodes/transform.py`). A node still carrying only the old `prompt_template`/`system_prompt`/`output_schema` fields keeps rendering through the generic form in B.2 below, untouched — nothing already saved is ever shown blank or silently reinterpreted. As of this update, every TransformAgent instance across all 6 of these workflows has been migrated to new-style, so in practice you will not see the legacy form anywhere in this specific set of files — but a brand-new workflow you build elsewhere could still contain either shape. |
| **AITaskAgent** *(not used in these 6 workflows, listed for completeness)* | `AITaskConfig.tsx` | Task-preset cards (Structured Extraction / Classification / Translation / Summarization / Draft Response / Custom), instructions textarea, output fields. | — |
| **EmailAgent** *(not used in these 6 workflows)* | `EmailConfig.tsx` | Operation picker, query/filter fields, send fields. | — |

### B.2 — The 26 "generic SchemaForm" node types actually used here

Field-by-field classification below comes straight from each type's live
config schema (`GET /api/node-types`), cross-checked live in the Builder for
several of them. **text** = plain input/textarea, **number** = number input,
**checkbox** = boolean toggle, **select** = dropdown, **text-list** =
newline-separated textarea, **JSON** = raw-JSON textarea (no structured
form).

| Node type | Category | Config fields → UI control |
|---|---|---|
| ~~**TransformAgent**~~ | ~~Control & Flow~~ | **Superseded — moved to B.1.** This row described the *legacy* shape (model:select, prompt_template:text, system_prompt:text, temperature:number, max_tokens:number, max_retries:number, output_schema:JSON) that a `TransformAgent` node falls back to only if it still carries a hand-written `prompt_template` and no `instructions`. Every TransformAgent instance in these 6 workflows has since been migrated off this shape — see the new-style row in B.1 and the corrected Part C entries below. Kept here, struck through, so a reader who only skimmed the old version of this table isn't left assuming `output_schema:JSON` still applies. |
| **HumanInLoopAgent** | Core | question:text, review_purpose:text, editable_content_field:text, allow_document_override:checkbox, max_edit_chars:number, **context_fields:JSON, review_panels:JSON, allowed_actions:JSON** (schema types these as arbitrary lists, not string-lists). Live-verified on 3 instances — always exactly 3 JSON textareas. This is the node type where the generic form leans on JSON the most. |
| **WorkflowFileLoader** | Control & Flow | files:text, max_chars_per_file:number, fail_on_unreadable:checkbox. Fully structured. |
| **Literal** | Control & Flow | value:**JSON** (schema has no fixed type for `value`, so even a bare string like `"ready"` must be typed as valid JSON, i.e. with quotes, in the Builder). |
| **TextAssemblerAgent** | Control & Flow | separator:text, **parts:JSON** (array of template refs). Live-verified on `draft_review_packet` — 1 JSON textarea. |
| **KimiVisionAgent** | Multimodal | image:text, prompt:text, vision_model:text, max_completion_tokens:number. Fully structured — live-verified, 0 JSON fields. |
| **OpenAIImageGenerationAgent** | Multimodal | prompt:text, backend:select, image_model:select, size:text, quality:select, output_format:select. Fully structured — live-verified, 0 JSON fields. |
| **FigureEmbedder** | Multimodal | content:text, **figures:JSON** (array of `{marker,image,alt_text}`). Live-verified — 1 JSON textarea. |
| **GraphNormalizer** | Proposal Engineering | model:select, max_tokens:number, concept_note:text, call_facts:text. Fully structured. |
| **ScientificResearchPlannerAgent** | Research & Discovery | model:select, standard_research_model:select, critical_research_model:select, max_briefs/max_total_tool_calls/standard_tool_calls/critical_tool_calls/max_skills_per_brief:number, **call_context:JSON, concept_context:JSON**. |
| **BoundedDeepResearchAgent** | Research & Discovery | research_briefs:text, everything else (max_jobs, max_parallel_jobs, max_total_tool_calls, max_tool_calls_per_job, max_citations_per_brief, max_candidates_per_claim, max_duration_seconds, max_iterations, max_cost_per_call_usd):number. No JSON fields — but note `research_briefs` is schema-typed as plain text even though it's semantically structured research-brief data; check what actually gets written there before assuming it's a simple string. |
| **ScholarlyCandidateDiscoveryAgent** | Research & Discovery | mcp_server:text, tool:text, sources:text-list, claim_types:text-list, model:select, require_contradiction_search:checkbox, everything numeric:number. No JSON fields. |
| **PriorProjectRetrieverAgent** | Evidence & Retrieval | research_briefs:text, sources:text-list, provider:select, only_prior_project_track:checkbox, rest numeric. No JSON fields. |
| **StructuredDatasetRetrieverAgent** | Evidence & Retrieval | queries:text, model:select, auto_plan_queries/fail_when_no_records:checkbox, rest numeric, **research_briefs:JSON, candidate_context:JSON**. |
| **InternalProjectEvidenceRetrieverAgent** | Evidence & Retrieval | source_text:text, model:select, query_internal_index/require_internal_index:checkbox, rest numeric, **source_registry:JSON, research_briefs:JSON, internal_index_filters:JSON**. |
| **ResearchSourceAcquirer** | Research & Discovery | candidates:text, rest numeric/checkbox, **policy:JSON**. |
| **PaperQAEvidenceSynthesizerAgent** | Evidence & Retrieval | documents/llm_model/summary_llm_model/embedding_model:text, rest numeric. No JSON fields. |
| **ProposalEvidenceFactoryAgent** | Proposal Engineering | candidates/documents/search_audit/rejected_candidates/citation_style:text, model:select, max_passages_per_document:number, **policy:JSON**. |
| **ProposalTruthGraphAgent** | Evidence & Retrieval | verified_claims/evidence_gaps/structured_data_records/internal_evidence_records/evidence_approval_decision:text, **blocking_issues:JSON, research_manifest:JSON**. |
| **CallCoverageMatrixAgent** | Proposal Engineering | *(no config properties at all — matches the YAML's `config: {}` on `call_coverage`)*. Configure tab shows only "Step id." Live-verified. |
| **ConceptAlternativesAgent** | Proposal Engineering | model:select, judge_model/concept_note:text. Fully structured. |
| **ConceptFreezeAgent** | Proposal Engineering | alternatives/selected_concept_id:text. Fully structured. |
| **MethodologyEngineeringAgent** | Proposal Engineering | model:select, research_briefs:text, max_objectives/max_skills_per_card:number, **selected_concept:JSON**. |
| **ConsistencyChecker** | Proposal Engineering | block_on_warn:checkbox. Fully structured (one field). |
| **ProposalSubmissionGate** | Proposal Engineering | proposal_text/evidence_blockers/consistency_gate/consistency_findings/evaluation_blockers:text, evaluation_threshold_passed/require_evaluation_pass/block_on_input_needed:checkbox, evaluation_total_score/minimum_proposal_characters:number, required_headings:text-list. No JSON fields — the whole node is structured despite looking complex on paper. |
| **ScientificSkillAgent** | Integrations | model:select, objective/system_prompt:text, skills:text-list, auto_select:checkbox, max_skills/temperature/max_tokens:number. Fully structured. |
| **HorizonEvaluationAgent** | Proposal Engineering | proposal_text/generator_model:text, evaluator_models:text-list, criterion_threshold/total_threshold:number. Fully structured. |
| **HorizonDOCXProposalRenderer** | Document Rendering & Export | content/metadata/citation_registry/evidence_qa/evidence_blockers:text, content_format:select, include_toc/include_bibliography/include_evidence_annex/enforce_page_limit/enable_footnotes:checkbox, page_limit/max_content_characters/max_embedded_image_bytes:number. Fully structured. |
| **HorizonHTMLProposalRenderer** | Document Rendering & Export | same shape as above minus the two `max_*_bytes` fields. Fully structured. |

**Reading this table for "what do I have to hand-edit YAML for":** scan a
node's row for any `:JSON` field — those are the ones where the Builder
gives you a textarea instead of real controls. Everything else in that row
is genuinely a UI-editable field, no YAML editor required.

---

## PART C — Per-workflow node inventory

Every node instance, cross-referenced back to Part B. "① / ⑥" markers = uses
one of the unconditional custom editors from B.1; a bare "(new-style)" next
to `TransformAgent` means this specific instance now qualifies for the new
conditional `PromptTemplateConfig` editor (see B.1's `TransformAgent` row) —
otherwise it's the generic form from B.2 (see that row for the
JSON/structured field split).

### C.1 — `pump_routing_level_1.yaml` (8 nodes, entry: `understand_message`, exit: 5 case nodes)

| id | type | Notes |
|---|---|---|
| `understand_message` | TransformAgent (new-style) | Classifies intent/language/lifecycle_stage/confidence. Migrated to the new editor: 2 Inputs (`subject`, `message`, each auto-wired to a workflow input of the same name), plain-language Instructions, and 8 structured Output fields — `intent` and `lifecycle_stage` are real `enum` fields with their allowed values spelled out (`NEW_PRODUCT_ENQUIRY | QUOTATION | ORDER_OR_DELIVERY | ...`, `presales | order_execution | installed_base | unknown`), not free-text. **Zero JSON textareas** — fully structured now, unlike when this table was first written. |
| `department_decision` | DecisionAgent ⑥ | 8 rules, fully structured rule builder. |
| `department_router` | RouterAgent ① | `mode: field`, 5 branches (SALES/SUPPLY_CHAIN/PRODUCT_SERVICE/CUSTOMER_SUPPORT/OTHER). |
| `sales_case`, `supply_chain_case`, `product_service_case`, `customer_support_case`, `other_case` | DataTransformAgent ⑥ | Each just shapes the final per-department output object; structured per-operation UI. |

### C.2 — `pump_routing_level_2.yaml` (19 nodes, entry: `understand_message`, exit: 7 nodes)

| id | type | Notes |
|---|---|---|
| `understand_message` | TransformAgent (new-style) | Same migration as Level 1's `understand_message`; `model` differs (`gpt-5.6-sol` here vs `gpt-5.6-terra` in Level 1 and `gpt-5.6-terra` again in Level 3, for the same node id, as of this update — this specific value has already changed at least once during this document's lifetime, so treat it as volatile and re-check the live YAML rather than trusting any model name written down here). |
| `find_customer`, `get_ownership`, `get_order`, `get_fulfilment`, `get_installed_unit` | MCPToolAgent ① | Each is a CRM lookup; `arguments` gets a tool-schema-driven structured form once server+tool are picked. |
| `customer_confirmed` | DataTransformAgent ⑥ | Fans out to the 4 lookup nodes above. |
| `business_context`, `routing_decision` | DecisionAgent ⑥ | Two-stage decision (facts, then routing) vs. Level 1's single decision node. |
| `department_router` | RouterAgent ① | `mode: field`. |
| `sales_owner_router`, `service_owner_router` | RouterAgent ① | `mode: conditions` — a *different* config shape from `department_router` despite being the same node type; pick the right mode first. |
| `sales_named_owner`, `sales_queue`, `supply_chain_case`, `service_named_owner`, `service_queue`, `customer_support_case`, `specialist_case` | DataTransformAgent ⑥ | Terminal case nodes. |

### C.3 — `pump_manufacturer_case_routing.yaml` (32 nodes, entry: `understand_message`, exit: 12 nodes)

The most complex of the 3 pump workflows — two extra Decision stages, three
Router "layers," and 4 Human Review nodes.

| id | type | Notes |
|---|---|---|
| `understand_message` | TransformAgent (new-style) | Same role as the other two files, migrated to the new editor too — the largest of the three, with 26 structured Output fields (vs. 8 in Level 1). |
| `find_customer`, `get_ownership`, `get_order`, `get_fulfilment`, `get_installed_unit`, `get_quote`, `get_availability` | MCPToolAgent ① | 7 CRM/ERP lookups (2 more than Level 2). |
| `customer_state_router` | RouterAgent ① | `mode: conditions`. |
| `customer_confirmed` | DataTransformAgent ⑥ | Fans out to all 6 lookup nodes. |
| **`business_facts`** | DecisionAgent ⑥ | **This is the node id a workflow author chose to call `business_facts` — it is NOT a platform feature named "Business Facts."** It's an ordinary DecisionAgent instance (11 defaults, 25 rules) like any other; edit it the same way as `department_decision`/`routing_decision` via the rule builder. If you're looking for a generic "Business Facts" UI panel elsewhere in the Builder, it doesn't exist — see Part A/B. |
| `routing_decision` | DecisionAgent ⑥ | Largest rule set in the file (~30 rules). |
| `assignment_decision` | DecisionAgent ⑥ | 7 rules; reads `routing_decision`'s output field `assignment_track` — this dependency is business-vocabulary coupling, not a graph edge, so it won't show up if you only look at the canvas connections. |
| `multi_intent_router` | RouterAgent ① | `mode: conditions`, 1 case (MULTI) + fallback (SINGLE). |
| `work_item_plan` | DecisionAgent ⑥ | 6 rules; first rule has no `when` at all (`default: true` — an unconditional rule), a shape not seen elsewhere. |
| `multi_intent_case` | DataTransformAgent ⑥ | Builds the multi-intent output packet. |
| `primary_department_router` | RouterAgent ① | `mode: field`, 5 branches. |
| `sales_owner_router`, `service_owner_router`, `support_router`, `other_router` | RouterAgent ① | Mix of `field` and `conditions` modes — check each individually before editing. |
| `sales_named_owner`, `sales_queue`, `supply_chain_case`, `service_named_owner`, `service_queue`, `support_case`, `specialist_case` | DataTransformAgent ⑥ | Terminal case nodes; 7 of these duplicate an ~8-field "outcome packet" block near-verbatim (see Part D) — a template/snippet feature would help here but doesn't exist today. |
| `service_serial_verification` | HumanInLoopAgent | approve/reject only (no edit), 4 review panels, 5 context fields — 3 JSON textareas. |
| `support_triage` | HumanInLoopAgent | approve/reject/**edit**, has `editable_content_field` — 3 JSON textareas. |
| `other_human_review` | HumanInLoopAgent | The only HITL node in this file **without** `review_panels` — falls back to `context_fields` only, so its Configure tab has fewer fields than its 3 siblings. |
| `customer_clarification` | HumanInLoopAgent | approve/reject/edit, 3 review panels. |

### C.4 — `horizon_partb_evidence.yaml` (33 nodes, entry: `load_concept`, exit: 4 nodes)

Stage 1 of the Horizon Part B pipeline. **No node in this file has an
`experience` block** (unlike every pump node) — Advanced-tab Guided Run
identity is currently blank for all 33 nodes.

| id | type | Notes |
|---|---|---|
| `load_concept`, `load_documents` | WorkflowFileLoader | Fully structured. |
| `understand_supporting_image` | KimiVisionAgent | Fully structured, 0 JSON fields. |
| `source_authority_curator`, `partb_metadata`, `call_intelligence`, `research_documentation`, `proposal_blueprint` | TransformAgent (new-style) | All 5 migrated to the new editor — `proposal_blueprint`'s Output fields (~30) and Instructions are the largest in any of the 6 files. **Honest limitation, not a mapping gap:** the original hand-written prompt described several of `proposal_blueprint`'s fields only in prose, with no enumerable sub-keys (`call_identity`, `page_and_format_rules`, `proposal_identity`, `source_hierarchy_and_fact_ledger`, and others) — the new Outputs editor can't express "arbitrary free-form object" the way the old raw `output_schema` dict could, so these became catch-all `{value: text}` objects, each flagged with a `# TODO(prompt-template-migration)` YAML comment rather than a fabricated shape. Worth a manual look if you rely on `proposal_blueprint`'s output downstream. |
| `approve_call_interpretation`, `evidence_blocked`, `approve_evidence_report`, `select_concept`, `approve_methodology`, `blueprint_blocked`, `approve_locked_blueprint` | HumanInLoopAgent | 3 JSON fields each (context_fields/review_panels/allowed_actions), except the "_blocked" nodes which omit `review_panels` similarly to `other_human_review` above. |
| `normalise_source_graph` | GraphNormalizer | Fully structured. |
| `research_plan` | ScientificResearchPlannerAgent | `call_context`/`concept_context` are JSON; rest structured. |
| `deep_research` | BoundedDeepResearchAgent | All numeric/text, no JSON fields. |
| `discover_candidates` | ScholarlyCandidateDiscoveryAgent | Has an extra top-level `data_protection_mode: public` key (per-node override, not in `config:`) — this key isn't covered by any of Part B's field lists since it sits alongside `config`, not inside it; check whether the Builder exposes top-level node keys other than `config`/`experience` before assuming it's editable (this pack didn't find a control for it). |
| `prior_project_research` | PriorProjectRetrieverAgent | No JSON fields. |
| `structured_dataset_research` | StructuredDatasetRetrieverAgent | Also has `data_protection_mode: public`; `research_briefs`/`candidate_context` are JSON. |
| `internal_project_evidence` | InternalProjectEvidenceRetrieverAgent | 3 JSON fields. |
| `acquire_research_sources` | ResearchSourceAcquirer | `policy` is JSON. |
| `synthesize_literature` | PaperQAEvidenceSynthesizerAgent | No JSON fields. |
| `verify_evidence` | ProposalEvidenceFactoryAgent | `policy` is JSON. |
| `truth_graph` | ProposalTruthGraphAgent | `blocking_issues`/`research_manifest` are JSON. |
| `evidence_readiness_router`, `blueprint_readiness_router` | RouterAgent ① | `mode: conditions` (READY/BLOCKED style rules). |
| `call_coverage` | CallCoverageMatrixAgent | Empty config — Configure tab shows only "Step id." |
| `concept_evolution` | ConceptAlternativesAgent | Fully structured. |
| `freeze_concept` | ConceptFreezeAgent | Fully structured. |
| `methodology_engineering` | MethodologyEngineeringAgent | `selected_concept` is JSON. |
| `evidence_consistency` | ConsistencyChecker | One checkbox. |
| `blueprint_completeness_gate` | ProposalSubmissionGate | Fully structured, no JSON. |

### C.5 — `horizon_partb_drafts.yaml` (16 nodes, entry: `start`, exit: 2 nodes)

Stage 2. Also has **no `experience` blocks** on any node.

| id | type | Notes |
|---|---|---|
| `start`, `drafting_start` | Literal | `value` is JSON-only even for a plain string. |
| `drafting_readiness_router` | RouterAgent ① | READY/BLOCKED conditions mode. |
| `drafting_blocked` | HumanInLoopAgent | 3 JSON fields, no `review_panels`. |
| `draft_1_1_objectives_ambition`, `draft_1_2_methodology`, `draft_2_1_pathways`, `draft_2_2_measures_and_2_3_summary`, `draft_3_1_workplan_resources`, `draft_3_2_consortium` | TransformAgent (new-style) | Migrated to the new editor. **No Output fields declared** on any of these 6 (unlike Stage 1's TransformAgents) — free Markdown output via `.raw`, so the Outputs section stays empty by design; these 6 nodes are the simplest possible use of the new editor (Inputs + Instructions only). |
| `generate_figure_oro_chain`, `generate_figure_impact_pathways`, `generate_figure_workplan`, `generate_figure_methodology` | OpenAIImageGenerationAgent | Fully structured, 0 JSON fields. |
| `draft_review_packet` | TextAssemblerAgent | `parts` is JSON (6-element array of refs). |
| `approve_drafts` | HumanInLoopAgent | 3 JSON fields. |

### C.6 — `horizon_partb_drafts_to_docx.yaml` (26 nodes, entry: `load_concept`, exit: 3 nodes)

Stage 3 — the only file with a **second router layer** (page-limit fork)
downstream of the readiness router, and 4 pairs of `_45`/`_40` sibling nodes.

| id | type | Notes |
|---|---|---|
| `load_concept` | WorkflowFileLoader | Structured. |
| `compile_excellence`, `compile_impact`, `compile_implementation`, `revise_excellence`, `revise_impact`, `revise_implementation` | TransformAgent (new-style) | Migrated to the new editor. No Output fields declared — free text via Instructions only, same simplest-case shape as the Stage 2 draft nodes above. |
| `compile_v1`, `final_revision` | TextAssemblerAgent | `parts` is JSON. |
| `scientific_peer_review` | ScientificSkillAgent | `skills` is a text-list (structured), fully structured overall. |
| `evaluate_v1`, `evaluate_final` | HorizonEvaluationAgent | Fully structured. |
| `red_team_v1` | TransformAgent (new-style) | Migrated to the new editor — 8 structured Output fields, fully structured (no JSON textarea), unlike when this row was first written against the old `output_schema` shape. |
| `submission_gate` | ProposalSubmissionGate | Fully structured; note `required_headings` is populated here (6 items) vs. empty in Stage 1's `blueprint_completeness_gate`. |
| `submission_readiness_router`, `page_limit_router` | RouterAgent ① | Two Router "layers" in sequence — READY/BLOCKED then PAGE_45/PAGE_40. Configure each independently. |
| `submission_blocked` | HumanInLoopAgent | 3 JSON fields, no `review_panels`. |
| `figure_embedder` | FigureEmbedder | `figures` is JSON (4-element array of `{marker,image,alt_text}`). |
| `render_45_start`, `render_40_start` | Literal | `value` is JSON-only. |
| `render_submission_docx_45`, `render_submission_docx_40` | HorizonDOCXProposalRenderer | Fully structured; identical field set, differ only in `page_limit` value (45 vs 40). |
| `render_submission_pdf_45`, `render_submission_pdf_40` | HorizonHTMLProposalRenderer | Fully structured; same 45/40 pairing pattern. |
| `approve_submission_package_45`, `approve_submission_package_40` | HumanInLoopAgent | 3 JSON fields each; `context_fields` mixes plain text refs and bare node-id refs (e.g. `render_submission_docx_45`). |

---

## PART D — Cross-cutting things worth hand-editing carefully

These aren't tied to one node — they're patterns that show up across
multiple files and are easy to get wrong by editing only the YAML or only
the UI in isolation:

1. **Router branch edges are generated, not drawn.** If you hand-edit a
   `{from, condition, branches:{...}}` edge in YAML to point a branch at a
   different node, the canvas will happily render the new connection — but
   the Router node's own Configure tab is the "real" source of truth the
   next time someone opens it in the Builder. Prefer editing the branch
   target through the Router's Configure tab so YAML and UI never disagree.
2. **`entry`/`exit` are derived, so don't hand-edit them in isolation.**
   Changing `entry:` in YAML without also making that node the actual
   graph-topological entry point (zero incoming edges) will just get
   overwritten the next time the workflow is opened and saved in the
   Builder.
3. **`library:`, `description:`, `use_case:`, `x_outcome_fragments:` are 100%
   YAML-only.** There is no path to editing any of these four from the
   Builder — not even indirectly. If a work item is "update the workflow's
   Library card summary/tags" or "add a new outcome fragment," it is a pure
   YAML-editing task, full stop.
4. **`experience` blocks are all-or-nothing per node, and all 66 Horizon
   nodes currently have none.** If Horizon workflows need to show up
   sensibly in Guided Run/Business View, that's ~66 nodes' worth of Advanced
   tab data entry (or 66 nodes' worth of YAML you could paste in bulk,
   copying the 5-key shape already used by all 59 pump nodes) — either way,
   it's the single largest piece of *missing* content across these 6 files.
5. **Same node id, different config across files is real and intentional
   here, not a mapping bug.** `understand_message`, `department_router`,
   `supply_chain_case`, `customer_support_case`, `find_customer` etc. exist
   in more than one file with materially different `config` — a YAML→UI
   mapping (or any tooling) keyed only on node id without also scoping by
   file will silently collide. Always scope by `(file, node_id)`.
6. **Three different template syntaxes coexist**: DecisionAgent
   `then.value` sometimes uses `$field.path` (copy a runtime value);
   DataTransformAgent/x_outcome_fragments use `{{field.path}}` Jinja-style;
   MCPToolAgent `arguments` use `{{outputs.node.parsed.x?}}` (trailing `?` =
   optional/safe-access). None of this is enforced by the Builder UI — it's
   on you to use the right syntax in the right field regardless of whether
   you're typing into a form box or a JSON textarea. A new-style
   `TransformAgent`'s `input_fields[].value` uses the same `{{inputs.x}}` /
   `{{outputs.node.field}}` syntax too, but — unlike all three cases above —
   you never type it yourself: the Prompt Template editor's "+ Add Input"
   generates the reference automatically from the variable name you give it.
   This is also the one field in the whole app where the resolved value is
   deliberately typed `Any`, not `str` — an unsupplied optional input
   resolves to `None` (rendered as an empty gap in the prompt) and a
   reference into a JSON-typed input or an upstream object/list output field
   resolves to a real dict/list (rendered as pretty-printed JSON in the
   prompt), rather than either case erroring or stringifying oddly.
7. **A node's top-level keys besides `config`/`experience`** — e.g.
   `discover_candidates`/`structured_dataset_research`'s
   `data_protection_mode: public` in the evidence file, or `selected_model`
   / `allowed_models` / `model_routing` on most TransformAgent-family nodes
   — sit outside the block SchemaForm renders from `config_schema`. Model
   routing does have a real UI (Advanced tab → "Model routing" section,
   shown only when `uses_ai`/`config.model` is present), but
   `data_protection_mode` specifically wasn't found wired to any control in
   this pass — treat it as YAML-only until proven otherwise.
8. **`RouterAgent` and its two config modes are the single biggest
   "same type, pick-the-right-shape-first" trap** in this whole set of
   workflows — 12 of the 33 node-type instances across all 6 files are
   Router nodes split roughly evenly between `mode: field` and
   `mode: conditions`, and the Configure form only shows the right controls
   once you've selected the matching mode card.
9. **`TransformAgent` now has the same "pick-the-right-shape-first" trap as
   `RouterAgent`, in a milder form.** Whether you get the fully-structured
   new editor or the old generic-form-plus-one-JSON-field experience depends
   entirely on whether `instructions` is non-empty — there is no mode toggle
   to look at, only the practical fact of which fields the node's YAML
   already has. Every node in these 6 workflows has been migrated to
   new-style as of this update, so this trap won't bite you *here*, but it
   will the moment you open an older workflow elsewhere that still has a
   hand-written `prompt_template`, or if you ever hand-write a new
   `TransformAgent` node's YAML directly with `prompt_template` instead of
   `instructions`/`input_fields`/`output_fields` — that node will render the
   legacy form, not the one described in B.1, until `instructions` is filled
   in through the UI (or by hand) for the first time.
