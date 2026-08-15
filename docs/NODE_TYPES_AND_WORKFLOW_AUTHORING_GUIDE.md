# Node Types & Workflow Authoring Guide

> **Purpose of this document.** This is a reference for building your own workflows and decision logic on this platform. It documents every registered node type, how the platform validates a workflow before it runs, how `{{...}}` templating works, and how deterministic decision/routing logic is authored. It is written to be uploaded as a source into a NotebookLM notebook (or read directly) so you can ask it questions like "which node do I use to branch on a confidence score?" or "why did my template reference fail preflight?"
>
> This document describes the platform as of the current codebase (`app/nodes/`, `app/runtime/`). No screenshots are included — every node's shape is described from its actual Pydantic schemas, not paraphrased.

---

## Table of contents

1. [What this platform is](#1-what-this-platform-is)
2. [Core concepts](#2-core-concepts)
3. [Workflow YAML anatomy](#3-workflow-yaml-anatomy)
4. [Templating reference (`{{...}}`)](#4-templating-reference-)
5. [Decision logic: Decision, Router, and the Rules/Conditions grammar](#5-decision-logic-decision-router-and-the-rulesconditions-grammar)
6. [Validation: how preflight checks a workflow before it runs](#6-validation-how-preflight-checks-a-workflow-before-it-runs)
7. [How to enhance or extend a node's behavior](#7-how-to-enhance-or-extend-a-nodes-behavior)
8. [Node catalog](#8-node-catalog)
   - 8.1 [Core Building Blocks](#81-core-building-blocks)
   - 8.2 [Control & Flow](#82-control--flow)
   - 8.3 [Research & Discovery](#83-research--discovery)
   - 8.4 [Evidence & Retrieval](#84-evidence--retrieval)
   - 8.5 [Proposal Engineering](#85-proposal-engineering)
   - 8.6 [Multimodal](#86-multimodal)
   - 8.7 [Document Rendering & Export](#87-document-rendering--export)
   - 8.8 [Integrations](#88-integrations)
   - 8.9 [Uncategorized](#89-uncategorized)
9. [Quick-reference index](#9-quick-reference-index)

---

## 1. What this platform is

Workflows are directed graphs of typed **nodes**, authored either visually in the Builder or as YAML, and executed by a LangGraph-based runtime. Every node is a Python class (a `NodeType` subclass) that declares three Pydantic schemas — what it reads, what it writes, and how it is configured — and one `async def run(...)` method. The Builder, the YAML compiler, and the pre-run validator ("preflight") all read the same three schemas, so a node's contract can never silently drift between what the UI shows and what actually executes.

The platform's design principle, stated directly in the node source comments, is: **a new business behavior should almost always be a new *configuration* of an existing node, not a new node type.** Two "Core Building Blocks" — `AITaskAgent` and `DecisionAgent` — exist specifically so that "extract a field," "classify an intent," "decide urgency from business rules" are all just different configurations of the same two node types, rather than one bespoke Python class per behavior. Nodes outside the core set (proposal drafting, evidence verification, document rendering, research discovery, etc.) are pre-built domain capabilities you drop in and configure, not primitives you compose logic from — that job belongs to the eight Core Building Blocks and the four Control & Flow nodes.

This repository's example workflows span two domains: an EU Horizon-proposal-writing pipeline (the large "Proposal Engineering" / "Evidence & Retrieval" / "Research & Discovery" node families) and an industrial customer-case-routing pipeline (`workflows/pump_manufacturer_case_routing.yaml`, built almost entirely from Core Building Blocks). The routing workflow is the clearer template if what you're building is closer to "understand a request, decide what to do about it, maybe call a business system, maybe ask a human" — which is most business automation.

---

## 2. Core concepts

### 2.1 The `NodeType` contract

Every node class declares (from `app/nodes/base.py`):

| Declared by every node | Purpose |
|---|---|
| `type_name` (`ClassVar[str]`) | The registry key used in YAML's `type:` field, e.g. `"DecisionAgent"`. |
| `config_schema` (Pydantic `BaseModel`) | The shape of the node's YAML `config:` block. Pydantic validates it **at workflow-compile time** — a malformed config fails before any node runs, not mid-execution. |
| `input_schema` | What this node reads from workflow state. In practice almost every node declares this as empty and instead reads values through templated config fields — the input schema exists mostly as a formal declaration point. |
| `output_schema` | What this node writes back into `state["node_outputs"][node_id]`. The compiler validates a node's real output against this schema after every run. |
| `description` (`str`) | One-line text shown in the Builder's node palette. |
| `run(self, state, resolved_config)` | The async method that actually executes. `resolved_config` is the node's config with every `{{...}}` template already substituted. |

Two optional class-level fields control how the node presents itself and how it's grouped:

- **`family`**: `"core"` (the 8 small, general-purpose nodes meant to be composed) or `"specialized"` (a pre-built domain capability). The Builder's palette groups on this so the "start here" vocabulary stays small.
- **`execution_kind`**: `"ai"` (a model decides), `"deterministic"` (code decides, repeatably, for free), `"external"` (something outside the platform changes — an email is sent, a CRM is written to, a web search runs), `"human"` (a person decides), or `"input"`/`"output"` (data crosses the workflow boundary). This is what makes "where does automation stop and a human/external system take over" visible directly on the canvas, instead of buried inside a node's config.

A node also has three optional preflight extension points it can override (see [§6](#6-validation-how-preflight-checks-a-workflow-before-it-runs)):
- `required_services(config)` — which platform services (`llm`, `email`, `mcp`, `object_store`, …) this node needs, so preflight can verify they're configured *before* a run starts.
- `preflight_output_fields(config)` — which dotted paths into this node's output are valid template targets (important for nodes with visually-authored, per-workflow output shapes, like `AITaskAgent`).
- `preflight_static_output_values(config)` — output fields preflight can already prove are a fixed (usually empty) value given the node's current config, so a bare reference to them is flagged as an authoring error rather than a runtime surprise.

### 2.2 Category, family, and `about`

The Builder's palette groups every node type into one of nine categories (`app/nodes/categories.py`): **Core Building Blocks**, **Control & Flow**, **Research & Discovery**, **Evidence & Retrieval**, **Proposal Engineering**, **Multimodal**, **Document Rendering & Export**, **Integrations**, and the catch-all **Other**. This document's [node catalog](#8-node-catalog) is organized the same way.

Every node also gets an **About tab** in the Builder's node inspector — `what` it does, `why` it exists, `receives`/`produces`, whether it `uses_ai` or performs an `external_action`, and optionally `presets` (pre-filled configuration starting points — never a distinct node type) and `operators`. The 8 core node types hand-author this dict. The ~40 specialized types don't — instead, `app/nodes/about_synthesis.py` derives a usable About tab for them automatically from the node's own schemas plus real adjacency mined from every workflow YAML already on disk (which node types most often precede/follow this one, and a link to a real example workflow). Nothing here is invented — every derived field is either a schema field name or a fact observed in a real workflow file.

### 2.3 The `experience` block (Guided Run copy)

A node in a YAML workflow may carry an `experience:` block alongside its `config:` — this is what a non-technical reviewer sees in "Guided Run" mode instead of raw node/field names:

```yaml
experience:
  display_name: Department Rules      # short, jargon-free label
  purpose: Decide which department handles this request, from the facts above.
  contribution: Every rule is readable, testable and the same every time.
  expected_output: A department, a case type and the reason the rules chose it.
  failure_message: The routing rules could not be evaluated.
```

Preflight enforces that any node visible to ordinary users (i.e. not marked `visibility: advanced`) fills in all five of these fields (`GUIDED_EXPERIENCE_INCOMPLETE`), and warns if `display_name` still contains technical jargon like "agent", "node", "payload", or an underscore (`GUIDED_COPY_TECHNICAL`) — see [§6](#6-validation-how-preflight-checks-a-workflow-before-it-runs).

---

## 3. Workflow YAML anatomy

A complete workflow file has this top-level shape (drawn from `workflows/pump_manufacturer_case_routing.yaml` and `workflows/test_fixtures/pump_routing_level_1.yaml`):

```yaml
name: Pump Customer Routing — Level 1 · Simple
description: >-
  Free-text description of what the workflow does. Rendered in the Library card.
version: '1.0'
use_case: industrial_customer_operations

# Library card metadata — governs how the workflow appears in the UI's catalog.
library:
  title: Pump Customer Routing — Level 1 · Simple
  summary: One or two sentences.
  purpose:                    # bullet list — what this workflow is for
    - Show the shape of the solution in under two minutes
  suitable_for:                # bullet list
    - A first demonstration to business stakeholders
  not_suitable_for:            # bullet list — sets expectations, prevents misuse
    - Production use — it has no access to the CRM or ERP
  outputs:                     # bullet list — what a run produces
    - Owning department and case type
  input_types:
    - Customer email or message text
  human_reviews:
    count: 0                   # how many HumanInLoopAgent gates this workflow has
    labels: []
  visibility_status: draft     # draft workflows are excluded from some UI surfaces

# Declared workflow inputs — every downstream node addresses these as {{inputs.<name>}}
inputs:
  message:
    type: text
    required: true
    description: The customer's message, in its original language.
  subject:
    type: text
    required: false

entry: understand_message      # the first node to run
exit:                          # every node id a run is allowed to terminate on
  - sales_case
  - supply_chain_case

# What a completed run returns as its final result payload
output:
  include_input: false
  nodes:
    - node_id: understand_message
      flatten: false
    - node_id: department_decision
      flatten: false

nodes:
  - id: understand_message       # unique node id — this is what {{outputs.<id>...}} addresses
    type: TransformAgent          # must be a registered type_name
    config:
      # node-specific config, validated against that type's config_schema
    experience:
      display_name: Understand Message
      purpose: ...
      contribution: ...
      expected_output: ...
      failure_message: ...

  - id: department_decision
    type: DecisionAgent
    config: { ... }

edges:
  - from: understand_message
    to: department_decision       # a plain, unconditional edge

  - from: department_router
    condition: route               # a conditional edge — always "route"
    branches:                      # route value → target node id
      SALES: sales_case
      SUPPLY_CHAIN: supply_chain_case
      OTHER: other_case
```

Key rules:
- **Node ids** must match `[A-Za-z_][A-Za-z0-9_]*` (no dashes, no dots) — they are also the first segment of every template path that addresses this node's output.
- **A plain edge** (`from`/`to`) always fires. **A conditional edge** (`from`/`condition`/`branches`) fires exactly one branch, chosen by whichever node produced a `route` output field — in practice this is always a `RouterAgent`, though any node type that declares a `route` output field can be the source (preflight's `CONDITIONAL_SOURCE_HAS_NO_ROUTE` check enforces this).
- **A node can have only one outgoing conditional edge** (`MULTIPLE_CONDITIONAL_EDGES`), but any number of plain edges (fan-out) — and a node with two or more plain-edge *predecessors* is an implicit AND-join: the compiler waits for all of them before running it.
- **`exit`** declares every node id a run is allowed to terminate on; a workflow with no path from every reachable node to some exit/terminal fails preflight (`NO_EXIT_PATH`).

---

## 4. Templating reference (`{{...}}`)

Implemented in `app/runtime/templating.py`, and resolved **at runtime**, not at compile time — preflight only checks that a reference is *plausible* given the graph shape, not that it will resolve on every single run.

### Syntax

```
{{ <dotted.path> }}
{{ <dotted.path>? }}      <- trailing "?" marks the reference OPTIONAL
```

Only word characters and dots are allowed inside the braces — no spaces inside the path, no filters, no expressions, no arithmetic.

- **Whole-value mode**: if the entire (trimmed) config string is exactly one `{{...}}` reference, the substitution returns the raw underlying value with its original type preserved — a number stays a number, a list stays a list, a dict stays a dict. Use this whenever a config field expects something other than a string, e.g. `arguments: { customer_id: "{{outputs.find_customer.first.account_id}}" }`.
- **Embedded mode**: if the reference sits inside a larger string (e.g. `"Hello {{inputs.name}}, your case is {{outputs.classify.result.priority}}"`), every match is substituted as text and the result is always a string.

### Valid path roots

- `inputs.<name>` — a declared workflow input.
- `variables.<name>` — a declared static workflow variable.
- `outputs.<node_id>.<field>...` — a node's output, through the canonical `outputs` virtual root.
- `<node_id>.<field>...` — legacy shorthand: if the first path segment matches a node id already present in `node_outputs`, `outputs.` is silently prepended. Both forms address the same value (you'll see both conventions in this repo's example workflows — `department_decision.decisions.department` and `outputs.department_decision.decisions.department` are equivalent).
- Numeric segments index into a list, e.g. `{{outputs.find_account.data.accounts.0.id}}` (negative indices work too, Python-style).
- Special system roots that always resolve without a node-id check: `session_id`, `collection_id`, `domain_state`, `workflow_id`, `workflow_name`.

### The `?` optional marker

- **Without `?`**, a path that fails to resolve raises at runtime and is treated as a hard workflow failure — this is deliberate: a template that silently resolves to nothing is how a workflow ends up producing a message addressed to "None".
- **With `?`**, a failed lookup resolves to `None` instead of raising. In whole-value mode this passes `None` straight through — a node reading an optional value sees "nothing was supplied" and can skip that step. In embedded-string mode, `None` renders as an empty string, never the literal text `"None"`.
- A resolved-to-`None` value is then dropped from the node's config *only if* that field accepts `None` or has a default — this lets the field's own default apply instead of failing with a confusing "input should be a valid string" error naming a field the workflow author never typed directly. A field that is genuinely *required* and resolves to `None` is left in place so it still surfaces as a real validation error.
- Example from the pump-routing production workflow: `arguments: { account_id: '{{outputs.customer_confirmed.data.account_id?}}' }` — an optional MCP tool argument that should simply be omitted from the call if the upstream lookup found nothing, rather than failing the whole node.

### Failure diagnostics

When a required (non-`?`) path fails to resolve, the error names exactly which segment failed and what was available at that point:

```
Template path not resolvable: outputs.find_account.data.accounts — failed at
segment 'accounts' under 'outputs.find_account.data'; available at that level:
['account_id', 'name']
```

If the cursor at the failing segment is a list, the message suggests indexing it (e.g. `outputs.find_account.data.0`).

### Gotchas

1. **Nested objects/lists just walk the dict/list structure** — there's no special nested-object syntax; `{{outputs.step1.result.customer.email}}` is a plain series of dict lookups.
2. **The `.items.` convention.** For a list of structured objects, a node's declared output schema exposes the *element* shape at `<list_field>.items.<subfield>` — e.g. `EmailAgent`'s `messages.items.subject`. At runtime, both a numeric index (`messages.0.subject`) and the `.items.` form work; preflight treats `accounts.0.account_id` and `accounts.items.account_id` as equivalent to a declared `accounts.items.account_id` schema entry.
3. **A node cannot reference its own output** inside its own config — it hasn't run yet (`TEMPLATE_SELF_REFERENCE`).
4. **A reference must come from a node guaranteed to run before the referencing node on every path** — not merely "some path reaches it." A reference into one branch of a router, read by a node reachable from *multiple* router branches, is only guaranteed on the branch that actually set it (`TEMPLATE_CONDITIONAL_UPSTREAM`, a warning, not a hard error — but a real risk if the other branch runs).
5. **A field whose declared type permits `None`** can be present as a key but hold `None`, which then fails nested traversal — e.g. `{{gate.content.text}}` when `content` is `None`. Preflight warns (`TEMPLATE_NULLABLE_NESTED_ACCESS`) but does not block this.
6. **Statically-empty fields.** Some fields are provably always the same fixed (usually empty) value given a node's *current config* — e.g. a `TransformAgent` with no configured `output_schema` always returns `{}` for `parsed`, and an `AITaskAgent` with no `output_fields` always returns `{}` for `result`. Referencing such a field is a hard preflight error (`TEMPLATE_STATICALLY_EMPTY_FIELD`), not a warning, because it can never carry real content.
7. **The `$path` convention** (specific to `DataTransformAgent`'s `object` operation): inside that one operation's `value` map, a string starting with `$` reads a live value (`$outputs.step.result.field`); an ordinary string is a literal. This exists because `object` is evaluated in test/simulation contexts where templating hasn't run — in a normal workflow, use the same `{{...}}` syntax you use everywhere else.

---

## 5. Decision logic: Decision, Router, and the Rules/Conditions grammar

This is the section most relevant to "my own decision logic." Two Core Building Blocks carry all deterministic business logic in this platform — **zero LLM calls, same answer every time, and every run records exactly which conditions were checked and what value each one saw.**

### 5.1 `DecisionAgent` — write business conclusions from facts

`DecisionAgent` evaluates an ordered list of `IF ... THEN ...` rules (with nested AND/OR/NOT) against upstream state and writes named conclusions — `human_review`, `urgency`, `department`, whatever your business logic needs.

```yaml
type: DecisionAgent
config:
  defaults:                       # values before any rule runs — always present in output
    department: CUSTOMER_SUPPORT
    urgency: normal
  rules:
    - name: Stopped production is critical
      when:
        operator: and
        conditions:
          - field: outputs.understand_message.result.production_stopped
            operator: is_true
      then:
        - field: urgency
          operation: set
          value: critical
    - name: Escalate low-confidence extraction
      when:
        operator: or
        conditions:
          - field: outputs.classify.result.confidence
            operator: less_than
            value: 0.6
          - field: outputs.classify.result.sentiment
            operator: equals
            value: angry
      then:
        - field: human_review
          operation: set
          value: true
  declared_fields: [human_review]   # extra output fields this step promises, even if no rule/default sets them
```

Important behavior: **all rules are evaluated in order** (not first-match-wins) unless a rule sets `stop_on_match: true` — because business policy is usually additive (both "escalate on low confidence" and "escalate on complaints" can independently apply, and both should show in the explanation). Facts set by an earlier rule are visible to later rules under the `decisions.` root, so `IF production_stopped THEN urgency = "critical"` followed by `IF decisions.urgency == "critical" THEN notify = true` works without an extra node. A rule can instead be marked `default: true` to always fire ("otherwise") — mutually exclusive with `when`.

Output: `decisions.<field>` (the resolved facts — the exact shape used downstream, e.g. `{{outputs.department_decision.decisions.department}}`), `matched_rules` (names of rules that fired), `explanation` (a full per-condition trace: field, operator, expected/actual value, matched or not), `summary` (human-readable lines — this is what a "why did this route?" view in the Builder renders).

Built-in presets (starting points, not new node types): **Confidence Gate** (route low-confidence AI output to a human), **Required Fields Check** (flag requests missing needed information), **Priority Rules** (derive urgency from business facts), **Custom** (empty rule set).

### 5.2 `RouterAgent` — choose a branch, with the reason recorded

`RouterAgent` writes the `route` field a conditional edge reads. It has four modes:

| Mode | How it decides | Cost |
|---|---|---|
| `field` | Map one field's value to a branch, via a `value → route name` dict. The common case: intent → department. | Free |
| `conditions` | First matching rule group (same AND/OR/NOT grammar as Decision) wins, for routes that depend on several facts at once. | Free |
| `rule` | Legacy string-expression rules (`"a.b == 'x'"`), kept for existing workflows. | Free |
| `llm` | Ask a model to choose, for genuinely fuzzy routing. | Costs tokens |

`field` and `conditions` are deterministic and produce an explanation the Builder renders directly on the canvas edge. **A new department, label, or threshold is a configuration change — never a new node type.**

```yaml
# field mode — the common case
type: RouterAgent
config:
  mode: field
  route_field: department_decision.decisions.department
  branches:
    SALES: SALES
    SUPPLY_CHAIN: SUPPLY_CHAIN
    OTHER: OTHER
  fallback: CUSTOMER_SUPPORT       # ALWAYS set this — see MISSING_DEFAULT_ROUTE below
```

```yaml
# conditions mode — branch on several facts at once
type: RouterAgent
config:
  mode: conditions
  fallback: CONTINUE
  cases:
    - route: AMBIGUOUS
      description: >-
        Several accounts match the name — taking the first match would attach
        this case to the wrong company's data, so nothing account-scoped runs
        until a person picks.
      when:
        operator: and
        conditions:
          - field: find_customer.count
            operator: greater_than
            value: 1
    # no `when` on the fallback — `fallback:` above covers "otherwise"
```

Output: `route` (the branch taken — read by the conditional edge), `reason` (human-readable, e.g. `"department = 'SALES'"` or a case's own `description`), `route_value` (the raw value behind a `field`-mode decision), `explanation`/`matched_conditions` (full trace, `conditions` mode), `used_fallback` (`true` when nothing matched and the fallback ran — the single most useful signal when a live run routes somewhere unexpected).

**Always set a `fallback`.** Preflight flags a `field`/`conditions`-mode router with no fallback (`MISSING_DEFAULT_ROUTE`) — routing an unexpected value to a human review step is usually the right choice, rather than letting the run fail mid-graph.

### 5.3 The Rules & Conditions grammar

Shared by `DecisionAgent.rules` and `RouterAgent`'s `conditions` mode (`app/runtime/rules.py`). Deliberately *not* an expression language — a condition is structured data (`field`, `operator`, `value`), which is exactly what makes it round-trip through a visual rule editor and be checkable by preflight.

**Operators:**
```
equals, not_equals, contains, not_contains,
greater_than, less_than, greater_or_equal, less_or_equal,
exists, does_not_exist, is_empty, is_not_empty,
in, not_in, is_true, is_false
```
- **Unary** (ignore `value`): `exists`, `does_not_exist`, `is_empty`, `is_not_empty`, `is_true`, `is_false`.
- **Set** (`value` is a list of alternatives): `in`, `not_in`.
- **Allowed per field type** (also what the Builder's editor offers, and what `RULE_TYPE_MISMATCH` enforces): `string`/`text`/`date` → equality/contains/existence/set ops; `enum` → equality/set/existence ops only; `number`/`integer` → adds greater/less-than; `boolean` → `is_true`/`is_false`/equality/existence; `list` → `contains`/`not_contains`/existence; `object` → existence only.
- **Missing-field semantics**: a missing field path always fails every operator except `does_not_exist` and `is_empty` — notably, `not_equals` and `not_contains` do **not** pass just because the data was never extracted, so an incomplete extraction can't silently look "different enough" to match a rule.
- `equals`/`not_equals` coerce sensibly (string `"true"` equals boolean `true`; numeric-looking strings compare numerically; strings compare case-insensitively after trimming). `contains` on a list of strings is case-insensitive.

**AND / OR / NOT nesting** — a `ConditionGroup` has an `operator` (`"and"` | `"or"` | `"not"`, default `"and"`) and a `conditions` list whose members are each a leaf condition (`field`/`operator`/`value`) or another nested group. A `not` group must have exactly one child. Nesting is unlimited.

```yaml
when:
  operator: and
  conditions:
    - field: outputs.extract.result.request_type
      operator: equals
      value: refund
    - operator: or
      conditions:
        - field: outputs.extract.result.amount
          operator: greater_than
          value: 500
        - field: outputs.extract.result.flagged
          operator: is_true
```

**Path resolution** accepts the same roots as templates (`outputs.<node_id>...`, `inputs.<name>`, `variables.<name>`, bare `node_id.field` shorthand, `domain_state`) and never raises — a missing path resolves to "not present," which is exactly what lets `exists`/`does_not_exist` work at all. The `items` segment addresses the element shape of a list of objects the same way templates do (`messages.items.subject`).

### 5.4 A complete worked example

`workflows/test_fixtures/pump_routing_level_1.yaml` is the cleanest end-to-end template for "understand → decide → route → hand off," and is worth reading in full. Its shape:

```
WorkflowInput (message, subject)
      ↓
TransformAgent "understand_message"   — AI: extract intent, lifecycle_stage, confidence, etc.
      ↓
DecisionAgent "department_decision"   — deterministic: 8 rules map (intent, lifecycle_stage,
                                         order_reference) → department, case_type, reason, next_action
      ↓
RouterAgent "department_router"       — field mode: department → one of 5 branches, fallback CUSTOMER_SUPPORT
      ↓  (conditional edge, one branch fires)
DataTransformAgent × 5                — one per department, building that department's hand-off packet
```

Two of the eight decision rules are the ones that make the design's point explicit: the same word ("price", "delivery") reaches a different department depending on `lifecycle_stage` and whether an `order_reference` was given — e.g. *"A technical question before buying is a Sales question"* fires only when `intent = TECHNICAL_SUPPORT` **and** `lifecycle_stage = presales`, while the plain `intent = TECHNICAL_SUPPORT` rule (no lifecycle condition) routes to Product Service. Rule order matters here only in the sense that more specific rules should be checked in addition to general ones — since Decision evaluates every rule (not first-match), each rule's own `when` needs to be precise enough not to fire when a more specific rule should be the one whose conclusion wins for that field. The production workflow (`workflows/pump_manufacturer_case_routing.yaml`) extends this same pattern with live Dynamics 365 lookups (`MCPToolAgent`) and human gates (`HumanInLoopAgent`) — e.g. a `conditions`-mode router branches to `AMBIGUOUS` when a CRM customer search returns more than one match (`find_customer.count > 1`), stopping the case for a human to pick the right account rather than guessing.

---

## 6. Validation: how preflight checks a workflow before it runs

Preflight (`app/runtime/preflight.py`, with business-logic checks delegated to `app/runtime/logic_preflight.py`) runs **entirely without an LLM call**, every time a workflow is saved/validated in the Builder and again before every run. It reports `error` (blocks the run) or `warning` (non-blocking) findings, each with a stable code. The API also offers a best-effort **autofix** for a subset of these — a deterministic repair pass, not a model call.

The tables below are the full reference — organize your debugging by looking up the exact code preflight reported.

### YAML / schema parsing

| Code | What it catches | How to fix |
|---|---|---|
| `YAML_DUPLICATE_KEY` | The same key appears twice in one mapping. | Remove the duplicate key. |
| `YAML_SYNTAX` | The YAML text doesn't parse at all. | Fix indentation/quoting/list-vs-mapping syntax. |
| `YAML_ROOT` | The top level isn't a single mapping. | Make the file one top-level YAML mapping. |
| `WORKFLOW_SCHEMA` | The parsed YAML doesn't satisfy the workflow contract (missing/extra/malformed top-level field). | Correct the field named in the reported path. |

### Node type / config sanity

| Code | What it catches | How to fix |
|---|---|---|
| `UNKNOWN_NODE_TYPE` | `node.type` isn't a registered node type. | Use one of the suggested close matches. |
| `NODE_ID_INVALID` | A node id has characters unsafe for a template reference. | Use only letters/digits/underscore, starting with a letter or underscore. |
| `UNKNOWN_NODE_CONFIG_FIELD` | The node's config sets a key its schema doesn't define. | Remove the field, or fix its spelling. |
| `NODE_CONFIG_INVALID` | The node's config fails Pydantic validation against its schema. | Match the Builder's config schema for that node type. |
| `NODE_CONSTRUCTION_FAILED` | The node object couldn't be constructed at all. | Inspect the reported exception; fix the node's config. |
| `MODEL_NOT_IN_CATALOG` | A node's selected model isn't in the approved catalog. | Choose an approved model, or a valid `openrouter/<vendor>/<model>` id. |
| `MODEL_ROUTE_INVALID` | A named model can't actually be routed. | Fix the model name/route. |
| `RAG_RUNTIME_FILTER_UNSAFE` | A `RAGAgent`/`KnowledgeRetrieval` runtime filter tries to override a reserved security/provenance field. | Remove that key — retrieval scope can only be narrowed, never widened, by a runtime filter. |

### Graph topology & routing

| Code | What it catches | How to fix |
|---|---|---|
| `EDGE_HAS_NO_TARGET` / `EDGE_TARGET_CONFLICT` | An edge defines neither, or both, of `to`/`branches`. | Use exactly one of `to` or `branches` per edge. |
| `CONDITIONAL_EDGE_INCOMPLETE` | An edge has `condition` without `branches`, or vice versa. | A conditional edge needs both. |
| `MULTIPLE_CONDITIONAL_EDGES` | A node has more than one outgoing conditional edge. | Merge into a single conditional edge. |
| `CONDITIONAL_SOURCE_HAS_NO_ROUTE` | A non-`RouterAgent` conditional-edge source declares no `route` output field. | Use `RouterAgent`, or add a typed `route` output. |
| `UNSUPPORTED_ROUTE_CONDITION` | A conditional edge's `condition` isn't `route`. | Set `condition: route` — it's the only value the compiler understands. |
| `ROUTER_DUPLICATE_RULE` / `ROUTER_MULTIPLE_DEFAULTS` | A `rule`-mode router has duplicate rule names, or more than one `default: true`. | Rename the duplicate; keep at most one default. |
| `ROUTER_BRANCH_MISMATCH` | A `rule`-mode router's rule names and the edge's branch names don't match 1:1. | Use identical names in `rules` and edge `branches`. |
| `ROUTER_BRANCH_WITHOUT_TARGET` | The router can return a route value the edge draws no branch for. | Draw a branch for every possible route, or remove that route. |
| `UNREACHABLE_BRANCH` (warning) | The edge declares a branch the router can never produce. | Rename the branch, or delete the edge. |
| `MISSING_DEFAULT_ROUTE` (warning) | A `field`/`conditions` router has no `fallback`. | Set a `fallback` — usually a human-review branch. |
| `UNREACHABLE_NODE` | A node can't be reached by walking forward from `entry`. | Connect it into the graph, or delete it. |
| `NO_TERMINAL_NODE` | No `exit` declared and every node still has an outgoing edge. | Declare an `exit`, or ensure some path terminates. |
| `NO_EXIT_PATH` | A reachable node has no path forward to any exit. | Add an edge from it toward a terminal/exit node. |
| `DUPLICATE_EDGE` | The same `from → to` edge appears twice. | Remove the duplicate. |
| `GRAPH_CYCLE` (warning) | The graph contains a cycle. | Fine only if there's a real, deterministic stop condition. |
| `FANIN_UNREACHABLE_ANDJOIN` / `ROUTER_JOIN_UNREACHABLE` | Two mutually-exclusive branches of the same router both feed one shared node as separate AND-join arrivals — since only one branch runs per request, the shared node waits forever for the other and the run silently reports "completed" without it ever firing. | Use one router with a case per outcome instead of routing into the shared node from two separate places; or give each branch its own downstream step. |

### Template reference checks

| Code | What it catches | How to fix |
|---|---|---|
| `TEMPLATE_UNBALANCED` | Mismatched `{{`/`}}` brace count. | Fix the braces. |
| `TEMPLATE_UNKNOWN_INPUT` / `TEMPLATE_UNKNOWN_VARIABLE` | `{{inputs.X}}`/`{{variables.X}}` references an undeclared input/variable. | Declare it, or fix the reference. |
| `TEMPLATE_UNKNOWN_NODE` | `{{outputs.X...}}` names a node id that doesn't exist. | Use a real node id (fuzzy-match suggestions are offered). |
| `TEMPLATE_SELF_REFERENCE` | A node references its own output. | Reference an upstream node instead. |
| `TEMPLATE_NOT_UPSTREAM` | The referenced node can never execute before the current one on any path. | Add the missing upstream edge, or fix the reference. |
| `TEMPLATE_CONDITIONAL_UPSTREAM` (warning) | The referenced node is upstream on *some* paths but not all (e.g. one router branch). | Reference a node common to every path, or move the reference. |
| `TEMPLATE_UNKNOWN_OUTPUT_FIELD` | The path's second segment isn't a declared top-level output field. | Use one of the listed available fields. |
| `TEMPLATE_STATICALLY_EMPTY_FIELD` | Given the node's current config, this field is provably always the same fixed (empty) value. | Reference a different field, or add the config that populates it. |
| `TEMPLATE_NULLABLE_NESTED_ACCESS` (warning) | The template traverses into a subfield of a field that may be `None`. | Confirm it always populates on every path reaching this node. |
| `TEMPLATE_UNKNOWN_STRUCTURED_FIELD` | For nodes with structured nested-field checking, a nested path matches no declared field. | Add the field to the output schema, or fix the path. |

### Workflow inputs

| Code | What it catches | How to fix |
|---|---|---|
| `REQUIRED_INPUT_MISSING` | A required input has no value (missing/empty). | Supply it when running the workflow. |
| `INPUT_TYPE_MISMATCH` | A `type: text` input got a non-string value. | Provide a string. |
| `FILE_INPUT_MULTIPLICITY` | A single-file input got more than one file. | Provide one file, or set `multiple: true`. |
| `FILE_INPUT_REFERENCE_INVALID` | A file input isn't a proper uploaded-file reference. | Upload through the platform rather than passing an arbitrary value. |
| `UNDECLARED_INPUT` (warning) | A run supplied an input the workflow doesn't declare. | Remove it, or declare it. |

### Guided Run authoring copy

| Code | What it catches | How to fix |
|---|---|---|
| `GUIDED_EXPERIENCE_INCOMPLETE` | A user-visible node is missing one of `display_name`/`purpose`/`contribution`/`expected_output`/`failure_message`. | Fill in the Guided tab, or mark the step `visibility: advanced`. |
| `GUIDED_COPY_TECHNICAL` (warning) | `display_name` contains jargon (`agent`, `node`, `llm`, `api`, underscores, slashes). | Rewrite as a short business action, e.g. "Map the call requirements". |
| `GUIDED_ROLE_MISSING` | `show_agent_role: true` but the role text is empty. | Name the role, or turn off `show_agent_role`. |

### Business logic — rules, routes, output-contract checks

| Code | What it catches | How to fix |
|---|---|---|
| `RULE_TYPE_MISMATCH` | A condition's operator doesn't apply to the field's declared type (e.g. `>=` on a list). | Use an operator the field's type actually supports. |
| `INVALID_ENUM_VALUE` | A rule/route compares an enum field against a value that isn't one of its allowed values. | Pick an allowed value, or add it to the upstream enum. |
| `INVALID_THRESHOLD` | A numeric comparison constant is outside the field's known range — the classic case is `confidence` (implicitly 0–1) compared against `80`. | Use `0.8`, not `80`, for a 0–1 confidence field. |
| `UNKNOWN_FIELD_REFERENCE` | A rule/route field doesn't resolve to any known value, or names a field the upstream node's typed output doesn't actually produce. | Use `outputs.<step>.<field>` and prefer picking from the field picker. |
| `AI_OUTPUT_NOT_AVAILABLE_UPSTREAM` | A rule/route reads from a node not guaranteed to run before the current one. | Connect the source so it's always upstream. |
| `REQUIRED_FIELD_MAY_BE_NULL` (warning) | A rule reads a required-but-nullable field that may be absent on some paths. | Add an `exists` condition first. |
| `EXTERNAL_ACTION_WITHOUT_REVIEW` (warning) | An `EmailAgent` send/reply, or an `MCPToolAgent` write, has no `HumanInLoopAgent` guaranteed upstream and (for MCP) no explicit `allow_unattended_write`. | Add a Human Review step upstream; use `create_draft` instead of `send`; or tick `allow_unattended_write` if intentional. |
| `MCP_TOOL_NOT_CONFIGURED` / `MCP_SERVER_NOT_CONFIGURED` / `MCP_TOOL_NOT_ALLOWED` / `MCP_WRITE_NOT_PERMITTED` | An `MCPToolAgent` has no server/tool selected, or the tool isn't permitted/registered for that connection. | Pick a configured server + allowed tool. |

### Graph compilation, live-service probes

| Code | What it catches | How to fix |
|---|---|---|
| `GRAPH_COMPILE_FAILED` | LangGraph's dry compile raised. | Fix the node/edge named in the exception. |
| `REQUIRED_SERVICE_MISSING` | A service a node needs isn't available in this environment. | Start the required dependency, then restart the API. |
| `OBJECT_STORE_UNAVAILABLE` / `OBJECT_STORE_CREDENTIALS_INVALID` | MinIO is unreachable, or its credentials are wrong. | Start MinIO / align credentials, then restart. |
| `LOCAL_MODEL_UNAVAILABLE` | A configured local model endpoint fails its health probe. | Start the endpoint; verify the served model name. |
| `RUN_HISTORY_UNAVAILABLE` | The audit/run-history database doesn't respond. | Start MongoDB; restart the API. |
| `MCP_SERVER_UNAVAILABLE` / `MCP_SERVER_PROBE_FAILED` / `MCP_TOOL_MISSING` | An MCP server a node depends on isn't running, can't be probed, or doesn't expose a configured tool. | Start the server / fix connectivity / pick a tool it actually exposes. |
| `WEB_SEARCH_PROVIDER_UNAVAILABLE` | A `WebSearchAgent` provider has no usable credentials. | Configure the relevant API key, or choose a different provider. |
| `IMAGE_GENERATION_UNAVAILABLE` / `KIMI_VISION_UNAVAILABLE` | No usable backend/credentials for image generation or Kimi vision. | Set the required API key, or disable the node's backend. |
| `COLLECTION_NOT_FOUND` / `COLLECTION_NOT_READY` | A referenced Knowledge collection doesn't exist, or has no active index. | Pick a real collection; run an ingestion and activate an index. |
| `RAG_AGENT_NOT_FOUND` / `RAG_AGENT_INACTIVE` | A referenced saved RAG Agent doesn't exist, or isn't active. | Pick a saved, active RAG Agent. |
| `MODEL_ACCESS_UNAVAILABLE` / `AUTO_MODEL_ACCESS_UNAVAILABLE` | The project can't actually use a selected (or any `Auto`-candidate) model. | Grant access, or choose/add an accessible model. |

### How this surfaces in the UI

Every save/validate in the Builder calls the same preflight function the runtime uses before a real run (`/api/workflows/validate` and friends). The Library view additionally shows a **readiness summary** per saved workflow (structural preflight only, no live-service probes) so you can see at a glance which workflows are ready to run without opening each one. There's also a best-effort **autofix** endpoint that applies deterministic repairs for a subset of preflight errors and re-validates — useful for quickly clearing mechanical issues (e.g. a missing `fallback`) without hand-editing YAML.

---

## 7. How to enhance or extend a node's behavior

**Default path — change configuration, not code.** The two node types built explicitly for this are `AITaskAgent` (any AI capability: extract, classify, summarize, translate, draft, analyze, rewrite, compare, evaluate) and `DecisionAgent`/`RouterAgent` (any deterministic business rule or branch). A new intent, a new department, a new confidence threshold, a new required field — every one of these is a **configuration change to an existing node**, authored in the Builder or in YAML, never a new Python class.

Two mechanisms make this practical:

- **Presets.** Several core nodes ship a curated set of `presets` — starting configurations, never new node types. `AITaskAgent` has "Structured Extraction", "Classification", "Translation", "Summarization", "Draft Response", "Custom". `DecisionAgent` has "Confidence Gate", "Required Fields Check", "Priority Rules", "Custom". `RouterAgent` has "Route on a field", "Route on conditions", "Route by model judgment". Pick the closest preset, then edit it — a preset is just pre-filled config.
- **The visual field/schema builder.** `AITaskAgent`'s `output_fields`, `WorkflowInputAgent`'s `fields`, and similar list-of-`FieldSpec` config fields are what let you define a new structured contract (a new extraction schema, a new set of workflow inputs) entirely through configuration — no code, and the resulting field paths become real, preflight-checked template targets (`result.<path>`, `data.<path>`) automatically.

**When configuration genuinely isn't enough** — the platform needs a wholly new integration surface (a new file format to extract text from, a new document-rendering template, a new external system class) rather than a new business rule. Two integration primitives absorb almost all of that:

- **`MCPToolAgent`** — a new business-system capability (CRM, ERP, any other system) is a new tool on an MCP server, discovered automatically by the Builder; it is *never* a new node type. If your business system already has (or can be given) an MCP server, this is almost certainly the right extension point rather than asking for a new node.
- **`EmailAgent`** — one node with an operation selector (`search`/`read`/`create_draft`/`reply`/`send`) replaces what would otherwise be a node type per provider per verb.

A genuinely new node type is only warranted when neither of those integration primitives nor a new AI Task/Decision/Router configuration can express the capability — in this codebase that line falls around document rendering, evidence verification pipelines, and multi-step research loops, which is why those exist as their own specialized node families (see the catalog below) rather than as `AITaskAgent` configurations.

**Validating your own additions.** Whatever you build, preflight (§6) is your feedback loop — save/validate in the Builder after each change rather than authoring a large graph blind. For decision logic specifically, `DecisionAgent`'s `explanation`/`summary` output and `RouterAgent`'s `reason`/`used_fallback` output are designed to be inspected: wire a debug/echo step (or just read the run trace) to confirm a rule fired for the reason you expect, not just that *some* rule fired.

---

## 8. Node catalog

Every registered node type, grouped by the Builder's palette category. `uses_ai`/`external_action` are the manifest's computed flags (see §2.2); `execution_kind` is `ai`/`deterministic`/`external`/`human`/`input`/`output`.

### 8.1 Core Building Blocks

The eight general-purpose nodes a new business workflow should normally be composed from.

#### WorkflowInputAgent
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `input` / `false` / `false`
- **What it does**: Declares what enters the workflow — a pasted message, an API payload, an uploaded document reference, an email, a prior workflow's output — and normalizes it into a typed object every downstream node can address the same way, regardless of how the run was started. Gives the canvas a visible starting point instead of beginning mid-air at an AI step, and makes the mapping picker/rule editor typed from the very first node.
- **Config fields**:

| Field | Type | Required/Default | What it means |
|---|---|---|---|
| `source` | `"manual"\|"api"\|"document"\|"email"\|"previous_workflow"` | Default `"manual"` | Where this workflow's input data comes from. |
| `fields` | `list[InputFieldBinding]` | Default `[]` | The declared shape of what enters the workflow. Each binding: `name` (how downstream nodes address it, `data.<name>`), `source` (state path, defaults to `inputs.<name>`), `type` (`string`/`text`/`number`/`integer`/`boolean`/`date`/`object`/`list`), `description`, `required`, `example`. |
| `sample` | `dict[str, Any]` | Default `{}` | Sample payload used by the Builder's Test tab/Simulator, so a half-built workflow can run end-to-end before real inputs are wired up. A real value always wins over the sample. |

- **Produces (output)**: `data.<field>` for each declared field, `source` (echoed), `missing` (declared-but-absent required fields — a workflow can route on this rather than hard-failing, e.g. "ask the customer for what's missing").
- **Validation / gotchas**: No config validators, but if `fields` is empty, `data` is statically `{}` (flagged by `TEMPLATE_STATICALLY_EMPTY_FIELD` if referenced). `required: false` fields simply resolve to `None` when absent rather than appearing in `missing`.
- **Presets**: one per source (`manual`, `api`, `document`, `email`, `previous_workflow`).
- **When to use it**: Always — it's the conventional entry node for a workflow, declaring the typed shape of the trigger data.

#### AITaskAgent
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `ai` / `true` / `false`
- **What it does**: One configurable AI step — extract, classify, summarize, translate, draft a reply, analyze, rewrite, generate, compare, or evaluate — with a typed, structured-output contract enforced through the model provider's native structured-output mechanism (not "please return JSON" in the prompt). This is the node that replaces a bespoke agent class per business behavior: the prompt, output schema, label set, language policy, and model are all configuration.
- **Config fields**:

| Field | Type | Required/Default | What it means |
|---|---|---|---|
| `task` | enum (`extract`\|`classify`\|`summarize`\|`translate`\|`analyze`\|`rewrite`\|`generate`\|`draft_response`\|`compare`\|`evaluate`\|`custom`) | Default `"extract"` | Which AI capability this step performs — determines the built-in task directive prepended to your instruction. |
| `instruction` | `str` | Default `""` | The task in your own words. |
| `input` | `str` | Default `""` | The content the task operates on — normally `{{outputs.previous.text}}` or similar. |
| `context` | `dict[str,str]` | Default `{}` | Extra labelled, templated context blocks, to keep `input` readable when the task needs several sources. |
| `model` | `str` | Default `"auto"` | Which model runs this step; `"auto"` routes to the platform's best fit. |
| `output_fields` | `list[FieldSpec]` | Default `[]` | The structured output shape — **required for `extract`/`classify`** (validated by a `model_validator`). |
| `language` | `LanguagePolicy` | Default (`input_language: auto, process_in_original_language: true, output_language: en, preserve_original: true`) | Reason over the original text, write output in the configured language, keep identifiers/product names verbatim. |
| `examples` | `list[AITaskExample]` | Default `[]` | Few-shot examples (`input`, `output`, `note`) to steer format/tone. |
| `include_confidence` | `bool` | Default `true` | Adds `confidence` + `reasoning` fields to the contract automatically, so a Decision/Router step can gate on uncertainty. |
| `temperature` | `float` (0.0–2.0) | Default `0.0` | Higher = more varied answers. |
| `max_tokens` | `int` (≥256) | Default `8192` | Output length ceiling. |
| `max_retries` | `int` (0–3) | Default `1` | Retries on schema-validation failure, with a correction prompt appended. |
| `fail_on_error` | `bool` | Default `true` | When `false`, a refusal/invalid-output/provider failure becomes a routable `status` instead of stopping the run. |
| `reasoning_effort` | `str \| None` | Default `None` | Optional low/medium/high override for models that support it. |

- **Produces (output)**: `result` (your own schema, under one key so it can never collide with a runtime field), `text` (free-text for non-structured tasks), `status` (`ok`\|`refused`\|`invalid_output`\|`provider_error` — four distinct outcomes, never collapsed into one exception), `error`, `confidence`, `reasoning`, `detected_language`, `model_used`, `attempts`.
- **Validation / gotchas**: `extract`/`classify` tasks without `output_fields` fail config validation immediately. With no `output_fields` at all, `result` is statically `{}` (`TEMPLATE_STATICALLY_EMPTY_FIELD` if referenced). On `fail_on_error: false`, a failed step reports `confidence: 0.0` (not `None`) — deliberately, so a downstream `confidence >= 0.8` gate reads a failure as "maximally uncertain" rather than silently evaluating a `None` comparison as false in a way that looks like the model was merely unsure.
- **Presets**: Structured Extraction, Classification, Translation, Summarization, Draft Response, Custom.
- **When to use it**: Any single AI capability — this is almost always your first choice for "I need the model to do X to this text."

#### DecisionAgent
See [§5.1](#51-decisionagent--write-business-conclusions-from-facts) for the full walkthrough.
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `deterministic` / `false` / `false`
- **Config fields**: `rules` (list of `{name, when: ConditionGroup, then: [Action], default, stop_on_match, description}`), `defaults` (`dict[str, Any]`, values before any rule runs), `declared_fields` (`list[str]`, extra output fields promised even if unset).
- **Produces (output)**: `decisions.<field>`, `matched_rules`, `explanation` (full per-condition trace), `summary` (human-readable lines).
- **Presets**: Confidence Gate, Required Fields Check, Priority Rules, Custom.

#### RouterAgent
See [§5.2](#52-routeragent--choose-a-branch-with-the-reason-recorded) for the full walkthrough.
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `deterministic` (`ai` only in `llm` mode) / `false` (`true` if `mode: llm`) / `false`
- **Config fields**: `mode` (`field`\|`conditions`\|`rule`\|`llm`), plus mode-specific fields (`route_field`+`branches` for `field`; `cases` for `conditions`; `rules` for `rule`/legacy; `model`+`prompt`+`context` for `llm`), and `fallback` (used by both deterministic modes).
- **Produces (output)**: `route`, `reason`, `route_value`, `explanation`, `matched_conditions`, `used_fallback`.
- **Presets**: Route on a field, Route on conditions, Route by model judgment.

#### DataTransformAgent
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `deterministic` / `false` / `false`
- **What it does**: Deterministic data shaping — rename a field, pick a few values out of many, normalize a unit, build an object for an API payload. No model call; using one for exact operations would add cost, latency and a failure mode for zero benefit. This is the node behind every "hand-off packet" step in the pump-routing example (see §5.4).
- **Config fields**: `operations` (`list[TransformOperation]`), `omit_empty` (`bool`, default `false` — drops keys whose computed value is empty/`None`, useful for an API that rejects explicit nulls). Each `TransformOperation` has `target` (the output key), `operation`, `source`/`sources` (input path(s)), `value` (literal/template/separator/field-list/key-map depending on operation), `multiply_by` (unit conversion), `default` (used when the source is missing/empty — recorded in the output's `defaulted` list, never silent), `description`.

  Operation kinds: `copy`, `constant`, `format` (template string), `join` (concat several sources with a separator), `coalesce` (first non-empty of several sources wins), `object` (build a nested object from a key→path map), `select` (pick named keys out of a source object), `number` (parse + optional unit conversion, tolerant of `"15 m³/h"`-style strings), `boolean`, `lowercase`, `uppercase`, `trim`, `count` (length of a list/dict/string), `split`.
- **Produces (output)**: `data.<target>` for each operation, `defaulted` (targets that fell back to their configured default — surfaced deliberately, since a mapping that quietly produces nulls is one of the hardest workflow bugs to notice).
- **Validation / gotchas**: A `model_validator` rejects a config where two operations write the same `target`. Each operation kind has its own required-input validation (e.g. `coalesce` needs `sources`, `format` needs a string `value`, `object` needs a dict `value`) — these fail at config-validation time with a specific message naming the offending target.
- **When to use it**: Any exact reshaping between two typed steps — especially the final step before handing a case to a system/queue/person, where you want a clean, minimal object rather than the full accumulated state.

#### HumanInLoopAgent
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `human` / `false` / `false`
- **What it does**: Pauses the run and waits for a person to approve, edit, or reject. The reviewer sees exactly the labelled panels you configure — this node is what makes "the author decides which actions may happen automatically" visible on the canvas, as a gate in front of an external action, rather than living inside a prompt. Uses LangGraph's `interrupt()`; state is checkpointed at the pause, and resuming replays as if the call returned normally.
- **Config fields**:

| Field | Type | Required/Default | What it means |
|---|---|---|---|
| `question` | `str` | Required | Templated question shown to the reviewer. |
| `context_fields` | `list[str]` | Default `[]` | Raw upstream fields shown as fallback context, when `review_panels` isn't set. |
| `review_panels` | `list[ReviewPanel]` | Default `[]` | Labelled panels the reviewer sees instead of a raw field dump. Each: `label`, `field` (dotted path), `hint`, `editable`. A panel whose path is absent is shown as "not available," not silently dropped. |
| `review_purpose` | `str` | Default `""` | Business-language explanation of why this gate exists, shown above the decision buttons. |
| `editable_content_field` | `str \| None` | Default `None` | State path shown in the rich-text editor; if the human edits and saves, this exact path is replaced before downstream nodes run. |
| `allow_document_override` | `bool` | Default `true` | Allow one uploaded, text-extractable document to replace the editor content. |
| `max_edit_chars` | `int` | Default `1,000,000` | Cap on accepted edited plain text. |
| `allowed_actions` | `list["approve"\|"reject"\|"edit"]` | Default all three | Which decisions the reviewer may make. |

- **Produces (output)**: `decision` (`approve`\|`reject`\|`edit`), `reason` (set on reject), `content`/`edited_content` (the reviewed document), `content_overridden` (true if a document upload replaced the editor content).
- **Validation / gotchas**: A decision not in `allowed_actions` is rejected at resume time. An `edit` decision requires `edited_content`; editor HTML is sanitized through a strict allow-list before being trusted. `editable_content_field` can only point at `inputs.*` (never `SYSTEM.*`) or an existing node's output — an arbitrary/invalid path raises rather than silently no-opping.
- **Presets**: "Approve before an external action", "Handle uncertain cases".
- **When to use it**: In front of any external action (send, write, disclose), or as the destination of a router's fallback/ambiguous branch — see the pump-routing production workflow's `service_serial_verification` gate for a fully worked review-panel example.

#### EmailAgent
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `external` / `false` / `true`
- **What it does**: One email capability with an operation selector, replacing what a platform usually grows into (a node type per provider per verb). The workflow references a connection id, never a token, so a workflow can be exported/versioned/shared without leaking mailbox access. Provider differences live in adapters beneath the contract.
- **Config fields**: `connection` (required — which configured mailbox), `operation` (`search`\|`read`\|`create_draft`\|`reply`\|`send`), plus operation-specific fields: search (`query`, `from_address`, `subject_contains`, `unread_only`, `has_attachments`, `folder`, `newer_than_days`, `max_results`), read/reply (`message_id`, `thread_id`), write ops (`to`/`cc`/`bcc` recipient lists, `subject`, `body`, `body_html`).
- **Produces (output)**: `operation`, `provider`, `connection`, `messages`/`message` (flattened `EmailMessageSummary`: `id`, `thread_id`, `subject`, `body`, `from_email`, `from_name`, `to`, `received_at`, `is_unread`, `attachment_count`, `attachment_names` — never raw attachment bytes), `message_count`, `draft_id`, `sent_message_id`, `deduplicated` (true if an idempotency check caught a would-be duplicate send).
- **Validation / gotchas**: A `model_validator` requires the operation's minimum inputs — `read` needs `message_id`; `reply` needs `message_id` or `thread_id`; `create_draft`/`send` need at least one recipient and a body. `search`/`read` are read-only; `create_draft`/`reply`/`send` are side-effecting — see the `EXTERNAL_ACTION_WITHOUT_REVIEW` preflight check in §6, which specifically watches `EmailAgent` sends/replies for a missing human-review gate.
- **Presets**: Search, Read, Create Draft, Reply, Send (each flags its own `external_action`).
- **When to use it**: Any mailbox interaction. Prefer `create_draft` over `send` unless a human review gate precedes this node.

#### MCPToolAgent
- **Category / execution_kind / uses_ai / external_action**: Core Building Blocks / `external` / `false` / `true`
- **What it does**: The platform's integration primitive — calls one named tool on one configured MCP server (e.g. "Dynamics 365 → Find Account") with author-mapped arguments, through a policy gate. A new business-system capability is a new tool on the server, discovered automatically; there is never a bespoke node class per system per verb. **Distinct from `MCPAgent`** (below, in Integrations): this node calls exactly the one tool the author selected — no model ever decides which tool to call or whether to call it.
- **Config fields**: `server_id` (required, configured connection id — never a URL/credential), `tool` (required), `arguments` (`dict[str, Any]`, normally template references), `fail_on_error` (default `true` — when `false`, a tool failure becomes a routable status), `timeout_seconds` (per-call override, ≤600s), `allow_unattended_write` (explicit author statement that this write may run without a human-review gate in front — does not itself grant write permission; the connection's own write policy still governs), `max_read_retries` (0–3; writes are never retried here).
- **Produces (output)**: `server`, `tool`, `operation`, `status` (`ok`\|`error`\|`denied`\|`needs_approval`\|`skipped`), `data` (the tool's typed result), `first` (first record of a collection result, or the single record), `text`, `count`, `found` (bool — "did the CRM know this customer?" without a Transform step), `is_structured`, `mode` (`live`\|`mock`), `duration_s`, `deduplicated`, `error`/`error_code`/`retryable`/`suggested_action`.
- **Validation / gotchas**: An argument that resolves to nothing is dropped if optional, but the whole call is **skipped** (status `skipped`, not an error) if a *required* argument resolves to nothing — e.g. a CRM lookup found no account, so there's no account id to fetch opportunities for; calling anyway would send a confusing null to the server. A write tool is refused unless the connection permits it and (unless `allow_unattended_write` is set) unless a human review has actually approved something earlier in the run's own execution path — read from real completed node outputs, never merely asserted by config.
- **When to use it**: Any read or write against a connected business system (CRM, ERP, or anything else exposed via MCP).

### 8.2 Control & Flow

Routing, transforms, and plain I/O — smaller and more mechanical than the Core Building Blocks, used as connective tissue.

#### Literal
- **Category / execution_kind**: Control & Flow / `input`
- **What it does**: Emits whatever literal value is in its config. Useful for smoke tests and stub values.
- **Config fields**: `value` (`Any`, required).
- **Produces (output)**: `value`.
- **When to use it**: Testing/scaffolding a workflow before a real upstream value exists.

#### Echo
- **Category / execution_kind**: Control & Flow / `deterministic`
- **What it does**: Renders a template string against state and returns it. Useful for testing templating in isolation.
- **Config fields**: `template` (`str`, required).
- **Produces (output)**: `text`.

#### TransformAgent
- **Category / execution_kind / uses_ai**: Control & Flow / `ai` (inferred — requires `llm`) / `true`
- **What it does**: The original, pure-LLM transform node (its name predates the Core/`AITaskAgent` split — kept registered, unchanged, for existing workflows). Summarize, classify, rewrite, extract, with an optional simple typed output schema (`{field_name: type_string}`, not the richer visual `FieldSpec` builder `AITaskAgent` uses).
- **Config fields**: `model` (default `"claude-sonnet-4-5"`), `prompt_template` (required), `system_prompt`, `output_schema` (`dict[str, str]` — type strings: `str`/`int`/`float`/`bool`/`list`/`dict`), `temperature` (default `0.2`), `max_tokens` (default `16384`), `max_retries` (default `1`).
- **Produces (output)**: `raw` (the model's raw text/JSON), `parsed` (`dict`, structured per `output_schema`).
- **Validation / gotchas**: With no `output_schema`, `parsed` is statically `{}` — a bare `{{step.parsed}}` reference is flagged (`TEMPLATE_STATICALLY_EMPTY_FIELD`); read `.raw` instead in that case. On repeated structured-output failure it raises after `max_retries + 1` attempts (no `fail_on_error` escape hatch, unlike `AITaskAgent`).
- **When to use it**: Prefer `AITaskAgent` for new work — it has the richer visual schema builder, four distinct failure statuses, confidence/language handling, and `fail_on_error` routing. Use `TransformAgent` only where a workflow already depends on it.

#### TextAssemblerAgent
- **Category / execution_kind**: Control & Flow / `deterministic`
- **What it does**: Deterministically joins already-generated text parts with a separator — **no LLM call**, specifically so a long final document can never lose content to a `max_tokens` ceiling. Asking a model to "re-assemble" an already-drafted multi-page document risks silent truncation (the call is capped by `max_tokens`, and there's no way to detect a cut-off response when no output schema is set); this node sidesteps that entirely by never calling a model at all.
- **Config fields**: `parts` (`list[str]`, default `[]`), `separator` (default `"\n\n"`).
- **Produces (output)**: `text`.
- **When to use it**: Assembling a long final document from several chunks that were each already generated within a realistic per-call token budget.

#### WorkflowFileLoader
- **Category / execution_kind**: Control & Flow / not explicitly overridden (falls back based on `required_services`; conceptually an input/ingestion step)
- **What it does**: Reads uploaded workflow files. Extracts text from supported PDFs, DOCX, PPTX, Markdown, spreadsheets, and code; images remain stable object-storage references (for multimodal nodes), never inlined as text.
- **Config fields**: `files` (`str \| WorkflowFileRef \| list[WorkflowFileRef]`, required — a template placeholder like `"{{inputs.source_files}}"` passes compile-time validation and is resolved before `run()`), `max_chars_per_file` (default `200,000`), `fail_on_unreadable` (default `false` — otherwise unreadable files are reported per-file rather than failing the whole node).
- **Produces (output)**: `text` (concatenated extracted text, each file prefixed `--- <name> ---`), `files` (list of `LoadedWorkflowFile`: `file_id`, `name`, `category`, `minio_key`, `extracted_chars`, `truncated`, `status` [`extracted`\|`unreadable`\|`failed`\|`image_reference`], `error`), `image_files` (raw file references for image category, untouched), `total_files`, `text_file_count`, `image_count`.
- **Validation / gotchas**: If `files` is still a plain string at `run()` time, that means the template never resolved to real uploaded references — a hard error. A file with no available text extractor for its extension is reported per-file (`status: "unreadable"`) rather than failing the run, unless `fail_on_unreadable: true`.
- **When to use it**: Right after an upload-type `WorkflowInputAgent`, to turn uploaded document references into usable text (and to separate out images for a vision node).

### 8.3 Research & Discovery

Finding candidate sources and papers. Everything in this category produces **candidates, never verified evidence** — always followed by acquisition + verification before use in a proposal.

#### BoundedDeepResearchAgent
- **Category / execution_kind / uses_ai / external_action**: Research & Discovery / `ai` / `true` / `true`
- **What it does**: Runs multiple K-Dense-guided bounded research dossiers using a web-search tool-calling LLM loop, with hard job/concurrency/tool-call/cost/duration limits. Takes a list of `ResearchBrief`s (typically from `ScientificResearchPlannerAgent`), runs them concurrently under strict ceilings, converts each dossier's citations into candidate sources tagged `discovery`/`contradiction`, deduplicates across dossiers, and records a `SearchAuditRecord` per claim/purpose.
- **Config fields** (all budgets, since this is the platform's most expensive-per-call node): `research_briefs` (required), `max_jobs` (default 8, ≤12), `max_parallel_jobs` (default 2, ≤4), `max_total_tool_calls` (default 72, ≤160), `max_tool_calls_per_job` (default 16, ≤40), `max_citations_per_brief` (default 20, ≤50), `max_candidates_per_claim` (default 12, ≤30), `max_duration_seconds` (default 1800), `max_iterations` (default 15, ≤50), `max_cost_per_call_usd` (default 15.0).
- **Produces (output)**: `dossiers` (per-brief results, citations, tool trace, usage), `candidates` (candidate-only `CandidateSource` records), `search_audit`, `jobs_completed`/`jobs_failed`, `failures` (`[{brief_id, error}]`), tool-call/token counters, `research_manifest`.
- **Validation / gotchas**: Raises up front if the plan exceeds this node's own budgets (size briefs from `ScientificResearchPlannerAgent` to fit inside them). A failed job doesn't fail the whole node — branch on `jobs_failed`, don't assume every configured brief produced a dossier.
- **When to use it**: The "gather" step in an evidence pipeline, right after a research planner, for state-of-the-art/policy/methodology/contradiction questions needing an LLM-guided search loop rather than a plain keyword fan-out.

#### WebSearchAgent
- **Category / execution_kind / uses_ai / external_action**: Research & Discovery / `external` / `false` / `true`
- **What it does**: A single live public-web query via Auto/Tavily/OpenAI/Kimi K3. Every hit is explicitly `status: "candidate_only"`.
- **Config fields**: `query` (required), `provider` (`auto`\|`tavily`\|`openai`\|`kimi`, default `auto`), `top_k` (default 8, ≤20), `fallback_to_openai` (default `true`).
- **Produces (output)**: `query`, `requested_provider`, `actual_provider`, `fallback_reason`, `results` (title/url/snippet/score/status), `result_count`.
- **When to use it**: One ad-hoc live lookup, as opposed to the multi-brief research loop above or the academic fan-out below.

#### ScholarlyCandidateDiscoveryAgent
*(defined in `app/nodes/evidence_agent.py`)*
- **Category / execution_kind / uses_ai / external_action**: Research & Discovery / `external` / `true` / `true`
- **What it does**: For each unresolved claim in the proposal graph, an LLM drafts a multi-query search plan (discovery + contradiction queries), fanned out to `paper-search-mcp` across configurable academic sources (arxiv, openalex, europepmc, core, openaire, zenodo, hal, doaj, pmc, semantic). Explicitly never writes to `Claim.verification` or `evidence_source_ids` — a hard boundary stated in its own docstring: "search metadata is a candidate set, not evidence."
- **Config fields**: `mcp_server` (default `paper-search-mcp`), `tool` (default `search_papers`), `sources` (list, sensible default set above), `max_results_per_source` (default 8), `max_candidates_per_claim` (default 20), `max_claims` (default 20), `claim_types` (default `[state_of_art, impact, problem, method]`), `require_contradiction_search` (default `true`), `model`, `query_planning_skill`, `max_duration_seconds`.
- **Produces (output)**: `candidates_found`, `claims_searched`, `candidates`, `search_audit` (includes per-source error map), `report`, `timed_out`, plus permanently-zero legacy display fields `sources_added`/`claims_linked` (real linkage only happens after full-text acquisition + verification).
- **When to use it**: The academic/scholarly discovery step for proposal claims, when you need broad multi-database candidate discovery feeding into acquisition/verification.

#### ResearchSourceAcquirer
- **Category / execution_kind / uses_ai / external_action**: Research & Discovery / `external` / `false` / `true`
- **What it does**: Resolves and stores candidate citations as **immutable** HTML/PDF source versions for exact-passage verification. Cross-lane deduplicates a candidate pool (from any discovery node), bounds selection per-claim and in total, fetches only public HTTP(S) hosts (hard-blocks `localhost`/`.local`/`.internal`/any non-global IP), extracts text, stores raw bytes + paginated text extract in object storage, and checks canonical/retraction status.
- **Config fields**: `candidates` (required), `policy` (`EvidencePolicy`, governs e.g. `max_download_bytes`), `max_concurrent_requests` (default 6), `max_sources_per_claim` (default 7), `max_total_sources` (default 60), `request_timeout_seconds` (default 45), `max_redirects` (default 4), `fail_when_none_acquired` (default `false`).
- **Produces (output)**: `candidates_processed`, `full_text_documents_acquired`, `documents` (`FullTextDocument`: hash-stamped, page-indexed, immutable), `rejected_candidates`, `report`.
- **Validation / gotchas**: A candidate pointing at a private/internal URL always becomes a rejected candidate, never a document — this is a safety control, not a bug. By default, a run where every candidate fails still "succeeds" with an empty `documents` list; set `fail_when_none_acquired: true` to hard-stop instead.
- **When to use it**: Between candidate discovery and claim verification, whenever you need immutable, hash-stamped full-text for exact-passage citation checking.

#### ScientificResearchPlannerAgent
- **Category / execution_kind / uses_ai / external_action**: Research & Discovery / `ai` / `true` / `false`
- **What it does**: Turns the call, the selected concept, and the proposal graph into several bounded research briefs, each routed through approved skills. Drafts a plan covering only the tracks that materially apply (state of art, EU policy, prior projects, methodology, adoption, environment, impact baselines, risks/contradictions), keeps only claim/requirement ids that actually exist in the graph, assigns a tool-call budget per brief tier, and selects skills via the skill catalog.
- **Config fields**: `call_context`, `concept_context` (free-form), `model` (default `gpt-5.6-terra`), `max_briefs` (default 8), `max_total_tool_calls` (default 72), `standard_tool_calls` (default 7), `critical_tool_calls` (default 11), `standard_research_model`/`critical_research_model`, `max_skills_per_brief` (default 4).
- **Produces (output)**: `research_briefs` (exactly the shape `BoundedDeepResearchAgent.research_briefs` expects), `brief_count`, `total_tool_call_budget` (check this against the downstream node's own `max_total_tool_calls`), `skills_manifest`, `skill_versions`, `unresolved_questions`, `governance_rules`.
- **Validation / gotchas**: Raises up front if `graph.claims` is empty ("run GraphNormalizer first"). A brief that links to no known claim id is silently dropped — if a whole draft produces zero usable briefs, raises.
- **When to use it**: The planning step immediately before `BoundedDeepResearchAgent`, once objectives/claims exist in the graph (post-`GraphNormalizer`).

### 8.4 Evidence & Retrieval

Pulling and verifying evidence for claims.

#### RAGAgent
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `ai` / `true` / `false`
- **What it does**: Hybrid retrieval + grounded, cited answer generation in one node. Two mutually exclusive modes: set `rag_agent_id` to delegate to a saved RAG Agent resource (retrieval + generation profile configured elsewhere — the recommended path for new workflows); or leave it unset and configure retrieval knobs directly. Either way it retrieves chunks, builds a `[1]`/`[2]`… labelled sources block, generates a grounded answer, and parses `[N]` citation markers back out — keeping only labels that map to a real retrieved chunk.
- **Config fields**: `rag_agent_id` (saved resource id — new workflows should set this), `model`, `query` (required), `runtime_filters` (only used with `rag_agent_id`; security scope is never overridable), `filters`/`top_k_candidates`/`top_n_final`/`alpha`/`rerank`/`compress` (only used *without* `rag_agent_id`), `generation_prompt`.
- **Produces (output)**: `answer`, `citations` (`label`, `chunk_id`, `source_doc`, `snippet`, `display_number`), `retrievals`, `rewritten_query`, `grounding_for_drafter`, `retrieval_trace_id`, plus timing/cost/token fields.
- **Validation / gotchas**: A `model_validator` raises if `rag_agent_id` is set **and** any legacy retrieval knob differs from its default — change the saved Retrieval Profile itself instead. If retrieval returns zero chunks, only `answer`/`citations`/`retrievals`/`rewritten_query` are populated.
- **When to use it**: General-purpose grounded Q&A over an indexed collection where you need a `[N]`-cited answer, not just retrieved chunks (contrast with `KnowledgeRetrieval` below, which retrieves without generating).

#### InternalProjectEvidenceRetrieverAgent
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `ai` / `true` / `false`
- **What it does**: Retrieves partner, pilot, work-plan, budget, and approved-internal-database facts — and requires **both** an exact source passage **and** explicit human approval before a fact may be used for drafting. Never lets a partner assertion masquerade as verified external scientific evidence.
- **Config fields**: `source_registry` (required — each source's class/approval status), `source_text` (required, `--- name ---`-delimited blocks), `research_briefs` (optional), `model` (default `gpt-5.6-terra`), `max_queries` (default 12), `max_records` (default 80), `max_source_chars` (default 300,000), `query_internal_index` (default `true`), `require_internal_index` (default `false`), `internal_index_filters`, `top_k_candidates`/`top_n_final`.
- **Produces (output)**: `records`, `approved_records`/`pending_human_approval` (each an `InternalEvidenceRecord` with `exact_passage`, `verification_status`, `drafting_allowed`), `rejected_facts`, `unresolved_questions`, `report`.
- **Validation / gotchas**: Only source blocks classified into one of four approved classes (human-approved fact, consortium/partner-supplied fact, approved internal database, structured dataset export) are ever extracted from — everything else is ignored. Every fact needs a verbatim `exact_passage`; `drafting_allowed` is only `true` when the source's own metadata is explicitly marked approved, regardless of passage match.
- **When to use it**: Consortium/partner/internal facts that must never be treated as independent external evidence, where a human sign-off is mandatory.

#### PriorProjectRetrieverAgent
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `deterministic` (no `llm` requirement, despite calling an external search API) / `false` / `false`
- **What it does**: Official-domain-restricted web searches (CORDIS, LIFE, EIP-AGRI) per research brief for prior-art comparison, non-duplication, and synergy positioning — output is candidate context only.
- **Config fields**: `research_briefs` (required), `sources` (`cordis`\|`life`\|`eip_agri`, default all three), `provider` (default `auto`), `max_briefs` (default 5), `max_results_per_source` (default 5), `max_candidates_per_claim` (default 8), `max_total_searches` (default 15), `max_parallel_searches` (default 3), `only_prior_project_track` (default `true`).
- **Produces (output)**: `candidates` (each `authority: "official_eu"`, `evidence_access: "metadata_only"`), `search_audit`, `projects_found`, `searches_completed`/`failed`, `verification_status: "candidate_only"`, `report`.
- **When to use it**: Surfacing related/prior EU-funded projects for non-duplication/synergy sections — always followed by acquisition + verification before citing any outcome.

#### StructuredDatasetRetrieverAgent
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `deterministic` (with default config) / `false` / `false`
- **What it does**: Retrieves bounded Eurostat/official structured data with explicit filters, immutable response snapshots, row hashes, count reconciliation, and auditable provenance. Optionally (`auto_plan_queries: true`) an LLM can draft bounded query contracts from proposal claims — but never invents a dataset code.
- **Config fields**: `queries` (explicit `StructuredDatasetQuery` contracts), `research_briefs`/`candidate_context` (for auto-planning), `auto_plan_queries` (default `false`), `model`, `max_queries` (default 8), `max_api_calls` (default 20 — exceeding this raises immediately, never silently truncates), `max_records` (default 5000), `max_response_bytes` (default 20MB), `request_timeout_seconds` (default 45), `fail_when_no_records` (default `false`).
- **Produces (output)**: `retrieval_contracts`, `records` (`StructuredDataEvidenceRecord` — every one has `human_review_required: true`, `drafting_allowed: false`, always), `quantitative_evidence_registry`, `audit` (includes count reconciliation), `unresolved_questions`, `report`.
- **Validation / gotchas**: A query referencing an unknown `claim_id` is dropped and reported, not silently ignored. This node can never itself unlock a record for drafting — that always requires a separate human-approval step.
- **When to use it**: Whenever a claim needs a specific, reproducible, auditable Eurostat figure rather than an estimated or scraped one.

#### PaperQAEvidenceSynthesizerAgent
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `deterministic` per the manifest's mechanical computation, though it does call LLMs internally via PaperQA2's own config (not through the platform's shared `llm` service) / `false` (undercounted) / `false`
- **What it does**: Runs PaperQA2 over **already-acquired** full-text documents, per claim, for per-document coverage and gap-aware literature synthesis. Deliberately never fetches sources itself (PaperQA2's own raw-fetch path has no SSRF guard or retraction check) — only ever calls `Docs.aadd_file` on bytes `ResearchSourceAcquirer` already downloaded safely.
- **Config fields**: `documents` (required, already-acquired `FullTextDocument`s), `llm_model`/`summary_llm_model` (default `gpt-5.6-luna`), `embedding_model` (default `text-embedding-3-small`), `evidence_k` (default 10), `max_claims` (default 15), `max_documents_per_claim` (default 12).
- **Produces (output)**: `results` (`ClaimSynthesis` per claim: `answer`, `formatted_answer`, `document_coverage`, `cost_usd`, `error`), `claims_processed`, `claims_skipped_no_claim_text`, `total_cost_usd`, `verification_status: "unverified_synthesis"`.
- **Validation / gotchas**: Output is explicitly *unverified* — always route through `ClaimEvidenceVerifier`/`ProposalEvidenceFactoryAgent` before treating a synthesis as an evidentiary claim.
- **When to use it**: Once full-text PDFs are acquired, for a gap-aware per-document coverage report — not a substitute for exact-passage verification.

#### MinIOEvidenceIngestion
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `external` / `false` / `false`
- **What it does**: Indexes acquired full-text sources (from their stored `pages.json`, never re-parsing the PDF) into the vector store, stamping each chunk with its global citation `display_number` so retrieved passages carry the correct footnote number all the way to the drafter. Runs after `CitationRegistryBuilder` and before any per-section `RAGAgent`.
- **Config fields**: `citation_registry` (the numbered registry — needs `citation_id`+`display_number`), `documents` (raw acquired documents — needs `pages_object_key`), `chunk_chars` (default 1200), `chunk_overlap_chars` (default 150), `max_chunks_per_source` (default 80), `max_total_chunks` (default 4000), `embed_batch_size` (default 64).
- **Produces (output)**: `sources_indexed`, `sources_skipped`, `chunks_written`, `collection_id`, `session_id`, `skipped_detail` (`{document_id, reason}`).
- **Validation / gotchas**: A document with no matching `display_number` in the registry, or missing `pages_object_key`, is skipped (not an error). De-duplicates to one physical source per `display_number`, so a paper cited by many claims is ingested once.
- **When to use it**: Immediately after `CitationRegistryBuilder`, before any `RAGAgent` that needs correctly-numbered retrieved passages.

#### ClaimEvidenceVerifier
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `ai` / `true` / `false`
- **What it does**: Verifies each proposal-graph claim already linked to `evidence_source_ids` against an exact passage in the source's stored text — an LLM stance/confidence verdict (`SUPPORTS`/`CONTRADICTS`/etc.), then rolls per-source relations up into a per-claim verdict.
- **Config fields**: `model` (default `claude-sonnet-4-5`), `minimum_support_confidence` (default 0.72), `max_source_characters` (default 24,000).
- **Produces (output)**: `claims_checked`, `relations_created`, `supported_claims`, `contradicted_claims`, `unverified_claims`, `findings` (`{claim_id, source_id, relation_id, stance, confidence, locator, reason}`). Also writes updated claim `verification` status and new evidence relations back into the shared proposal graph via a `__state__` side-channel (not a template-addressable output field).
- **Validation / gotchas**: Claims with no `evidence_source_ids` are skipped entirely (not counted). A claim's status becomes `ADDRESSED` only if a strong `SUPPORTS` relation exists **and** no strong `CONTRADICTS` relation does.
- **When to use it**: After claims already have candidate `evidence_source_ids` in the graph, to turn "linked but unverified" into a graph-level verdict.

#### ProposalTruthGraphAgent
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `deterministic` / `false` / `false`
- **What it does**: Freezes the drafting-safe truth graph — a pure, no-LLM, no-network JSON snapshot plus a SHA-256 integrity hash — containing only verified/qualified evidence links, plus explicit gaps and approval items. This is the single point where a human "approve" decision unlocks drafting.
- **Config fields**: `verified_claims` (required), `evidence_gaps`, `blocking_issues`, `research_manifest`, `structured_data_records`, `internal_evidence_records`, `evidence_approval_decision` (must equal `"approve"`, case-insensitive, to unlock).
- **Produces (output)**: `truth_graph` (full snapshot dict), `integrity_sha256`, `verified_claim_ids`/`qualified_claim_ids`/`excluded_claim_ids`, `human_review_queue`, `evidence_gaps`, `blocking_issues`, `drafting_allowed`, `approval_required`, `report`.
- **Validation / gotchas**: `drafting_allowed` is `true` only when there's at least one accepted claim, the approval decision is exactly `"approve"`, `blocking_issues` is empty, and no gap is marked blocking.
- **When to use it**: As the terminal evidence gate immediately before any drafting node — drafting nodes should template against `truth_graph.claims`/`drafting_allowed`, never raw evidence lists.

#### CitationRegistryBuilder
- **Category / execution_kind / uses_ai / external_action**: Evidence & Retrieval / `deterministic` / `false` / `false`
- **What it does**: Deterministically reshapes acquired documents into a numbered, renderer-ready citation registry — folding `canonical_url` into `formatted_citation` (the only place the DOCX/HTML renderers surface it), de-duplicating physically identical sources to one `[N]` each, and emitting a `claim_to_numbers` map plus a compact drafting guide.
- **Config fields**: `documents` (default `[]`), `require_full_text` (default `true` — excludes any document whose `evidence_access` isn't `full_text`).
- **Produces (output)**: `citation_registry` (`display_number`, `citation_id`, `formatted_citation`, `canonical_url`, `claim_ids`, …), `claim_to_numbers`, `drafting_guide`, `total_sources`, `total_documents_considered`, `excluded_documents`.
- **Validation / gotchas**: Must run before `MinIOEvidenceIngestion` (which depends on this registry's numbering) and before any drafting node that emits `[N]` markers.
- **When to use it**: Immediately after evidence acquisition, to establish the one stable citation numbering everything downstream must match.

### 8.5 Proposal Engineering

The EU-Horizon-proposal-specific reasoning/drafting/gating agents. All nine read and/or write a shared, typed **proposal graph** (`state["domain_state"]["proposal_graph"]`) — a node's own declared output fields land in `node_outputs` as usual, but graph updates travel through a separate `__state__` side-channel that is never itself a valid template target.

#### GraphNormalizer
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `deterministic` (pinned in the category table despite calling an LLM — the classification reflects that validation, not the LLM call, is what's being described) / `true` / `false`
- **What it does**: The keystone node — an LLM extracts structure from a concept note (+ optional call facts) into 14 typed collections (`Objective`, `WorkPackage`, `Partner`, `Claim`, `CallRequirement`, `KPI`, `Risk`, `ComplianceObject`, `Innovation`, `Result`, `Outcome`, `Impact`, `Task`, `OpenQuestion`); each is Pydantic-validated in the same node, so a malformed extraction is dropped with a warning rather than corrupting the graph. Explicitly instructed never to invent partners/numbers/objectives — anything absent goes to `open_questions`.
- **Config fields**: `model` (default `claude-sonnet-4-5`), `max_tokens` (default 16384), `concept_note`/`call_facts` (optional direct text, else read from `state["inputs"]`).
- **Produces (output)**: `result.counts` (per-collection extracted item counts), `result.warnings` (dropped/invalid items), `result.report`. The graph delta itself is written via `__state__`.
- **When to use it**: The entry point of a proposal workflow, turning a raw concept note into the structured graph every other Proposal Engineering node reads from.

#### ConceptAlternativesAgent
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `ai` / `true` / `false`
- **What it does**: Generates three independently-drafted concept postures (conservative/balanced/ambitious) in isolated LLM calls (so later postures can't anchor on the first), each scored on 4 deterministic dimensions (call coverage, evidence strength, expected-outcome contribution, feasibility) plus 5 qualitative dimensions scored by a second, independent judge model with adversarial critique — the generating model never grades its own work.
- **Config fields**: `model` (default `claude-opus-5`), `judge_model` (optional), `concept_note` (grounding prose).
- **Produces (output)**: `result.alternatives` (exactly 3, one per posture, each with `composite_score`, `evidence_weighted_score`, and 5 qualitative scores), `result.recommended_concept_id` (highest composite score).
- **Validation / gotchas**: Requires exactly one alternative per posture (validator enforces this).
- **When to use it**: After objectives/call requirements exist, before a human selects/freezes a single concept.

#### ConceptFreezeAgent
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `deterministic` / `false` / `false`
- **What it does**: Resolves the human gate's concept decision against the three generated alternatives — **no LLM call, fails closed** on an unrecognized id, so a typo in the human's edit can never silently fall back to a default.
- **Config fields**: `alternatives` (required, from `ConceptAlternativesAgent`), `selected_concept_id` (required, from the human gate's decision).
- **Produces (output)**: `result.selected_concept` (the single matching `ConceptAlternative`).
- **Validation / gotchas**: Raises, naming the invalid id and listing valid ones, if `selected_concept_id` doesn't match any alternative's id.
- **When to use it**: Immediately after the concept-selection human gate.

#### MethodologyEngineeringAgent
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `ai` / `true` / `false`
- **What it does**: Produces one skill-guided Method Card per graph objective (method, baseline, validation, uncertainty handling, failure condition), grounded only in verified claims and known research questions — instructed to set `status: "information_required"` rather than fabricate a field it can't determine.
- **Config fields**: `model` (default `claude-opus-5`), `research_briefs`, `selected_concept` (prose-only grounding), `max_objectives` (default 10), `max_skills_per_card` (default 2).
- **Produces (output)**: `result.method_cards` (one per objective, `status`: `drafted`\|`information_required`\|`needs_review`), `result.objectives_processed`, `result.skills_manifest`.
- **Validation / gotchas**: Raises if the graph has no objectives ("run GraphNormalizer first"). Any `research_question_id`/`evidence_claim_ids` the model invents outside the known sets are silently dropped, not surfaced as an error — check `status` fields to catch this.
- **When to use it**: After objectives exist (ideally after concept freeze), before drafting the Excellence/methodology sections.

#### ProposalEvidenceFactoryAgent
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `ai` / `true` / `false`
- **What it does**: A PaperQA2/Valsci-style claim-verification pipeline that never lets one unrestricted model call decide everything: deterministic passage retrieval → per-pair typed verdict classification → quote-exists-in-source verification (fails closed to `insufficient`/`0.0` confidence if not) → deterministic independence/canonical-metadata/contradiction/retraction gates per claim (materiality-aware) → only claims passing every gate are exposed as verified in the cited markdown/bibliography.
- **Config fields**: `candidates` (required), `documents` (required), `search_audit`, `rejected_candidates`, `policy` (`EvidencePolicy` — independence counts, confidence threshold, exact-locator requirement, etc.), `model`, `citation_style`, `max_passages_per_document` (default 7).
- **Produces (output)**: `result.verified_claims` (each with `final_status`: `verified`\|`verified_with_qualification`\|`partial`\|`mixed`\|`contradicted`\|`insufficient`\|`not_found`), `result.citation_registry`, `result.claim_evidence_links`, `result.proposal_ready_cited_markdown`, `result.bibliography_markdown`/`bibtex`, `result.evidence_gaps`, `result.qa_report`, `result.blocking_issues`.
- **Validation / gotchas**: A verifier's `exact_quote` must be found verbatim in the retrieved passage or the pair is forced to `insufficient` — a paraphrased quote is a common reason a claim doesn't verify. `verified`/`verified_with_qualification` requires *all* applicable gates to pass, not just one supporting link.
- **When to use it**: After claims exist in the graph and candidate sources + full-text documents have been discovered/fetched, to turn raw candidates into an auditable, fail-closed verified claim set.

#### ConsistencyChecker
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `deterministic` / `false` / `false`
- **What it does**: A pure, no-LLM gate over the typed proposal graph — nine hard rules (R1–R9): every KPI has an owner/baseline/target/date resolving to a real partner; every objective maps to a work package; every work package/task references a real partner; every expected-outcome requirement is addressed by an outcome; the four mandatory compliance dimensions (gender, SSH, open science, ethics) aren't `MISSING`; every partner has `legal_name`+`country`; no submission-blocking open question remains.
- **Config fields**: `block_on_warn` (default `false` — if `true`, any advisory finding escalates the verdict to `BLOCK`).
- **Produces (output)**: `result.gate` (`PASS`\|`WARN`\|`BLOCK`), `result.findings` (`{rule, blocking, message}`), `result.report`.
- **When to use it**: After the graph has KPIs/work packages/partners/compliance populated, right before final assembly/submission — typically feeds `ProposalSubmissionGate`'s `consistency_gate`/`consistency_findings`.

#### CallCoverageMatrixAgent
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `deterministic` / `false` / `false`
- **What it does**: For every call requirement, deterministically computes `ADDRESSED`/`PARTIAL`/`MISSING` by checking section mapping, graph-object mapping, linked outcomes (for expected-outcome requirements), and whether all linked evidence claims are verified with sufficient confidence and no contradictions. Requirements of kind `hard_eligibility`/`expected_outcome`/`must_address` that aren't fully addressed block submission.
- **Config fields**: none — entirely a function of the graph's current state.
- **Produces (output)**: `result.rows` (per-requirement: `status`, `missing_items`, `blocking`), `result.addressed`/`partial`/`missing` counts, `result.coverage_percent`, `result.blocking_requirement_ids`, `result.submission_blocked`.
- **When to use it**: Late, after drafting and evidence verification have populated the graph, feeding forward into `ProposalSubmissionGate`/`HorizonEvaluationAgent`.

#### HorizonEvaluationAgent
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `ai` / `true` / `false`
- **What it does**: Scores Excellence/Impact/Implementation with two independent, cross-provider evaluator models, combined with deterministic evidence-based blockers computed from the call-coverage matrix. Enforces that the two evaluators come from two different providers and that neither is the same model that drafted the proposal — so the model never grades its own writing.
- **Config fields**: `proposal_text` (required), `generator_model` (for the independence check), `evaluator_models` (default `[claude-sonnet-4-5, gpt-5]` — must be exactly two, two different providers), `criterion_threshold` (default 3.0), `total_threshold` (default 10.0).
- **Produces (output)**: `result.criteria` (per-criterion panel), `result.total_score`, `result.threshold_passed`, `result.coverage_percent`, `result.deterministic_blockers`, `result.high_disagreement_criteria`.
- **Validation / gotchas**: Raises if the two evaluator models aren't distinct/cross-provider, or if the generator model is also an evaluator.
- **When to use it**: After the proposal is drafted (ideally after evidence verification), feeding `ProposalSubmissionGate`'s evaluation fields directly.

#### ProposalSubmissionGate
- **Category / execution_kind / uses_ai / external_action**: Proposal Engineering / `deterministic` / `false` / `false`
- **What it does**: Combines every hard, auditable check into one release decision — **never calls an LLM**, and never lets a high evaluator score hide missing evidence, graph inconsistencies, incomplete sections, or visible `[INPUT NEEDED]` markers. Runs six named checks and produces `READY`/`BLOCKED`.
- **Config fields**: `proposal_text` (required), `evidence_blockers`, `consistency_gate` (from `ConsistencyChecker`), `consistency_findings`, `evaluation_threshold_passed`/`evaluation_total_score`/`evaluation_blockers` (from `HorizonEvaluationAgent`), `required_headings` (default `[excellence, impact, implementation]`), `minimum_proposal_characters` (default 8000), `require_evaluation_pass` (default `true`), `block_on_input_needed` (default `true`).
- **Produces (output)**: `result.status` (`READY`\|`BLOCKED`), `result.submission_ready`, `result.blockers`, `result.warnings`, `result.checks` (per named check), `result.input_needed_count`, `result.report`.
- **Validation / gotchas**: Ignores `state` entirely — must be wired via templates to every upstream signal. Only a `consistency_gate` of exactly `"BLOCK"` (case-insensitive) blocks; `"WARN"` is only a warning.
- **When to use it**: As the final gate before submission/export — wire it downstream of `ConsistencyChecker`, `ProposalEvidenceFactoryAgent`, and `HorizonEvaluationAgent` so none of those signals can be silently bypassed.

### 8.6 Multimodal

Vision and image generation.

#### KimiVisionAgent
- **Category / execution_kind**: Multimodal / `ai` (explicit override; note the manifest's mechanical `uses_ai` flag reads `false` since `required_services` returns `{object_store, kimi_vision}`, not `llm` — the node genuinely calls a vision model despite the badge)
- **What it does**: Analyzes one uploaded image with Kimi K3. Image bytes are fetched from object storage and are **never** written to workflow state/history. If no image was supplied for an optional image input, returns a clean `skipped: true` result instead of failing.
- **Config fields**: `image` (`str \| WorkflowFileRef \| None`, required — `None` is valid, meaning "not supplied this run"), `prompt` (default `"Describe and analyse this image."`), `vision_model` (fixed `"kimi-k3"`), `max_completion_tokens` (default 8192, ≤32768).
- **Produces (output)**: `analysis`, `provider` (`"kimi"`), `model`, `image_name`, `minio_key`, `content_type`, `byte_size`, `input_tokens`/`output_tokens`, `skipped`.
- **Validation / gotchas**: Raises if `image` resolves to a plain unresolved-template string, or if the referenced file's category isn't `"image"`.
- **When to use it**: A written description/analysis of one uploaded image, without ever exposing raw image bytes to workflow state.

#### OpenAIImageGenerationAgent
- **Category / execution_kind**: Multimodal / `ai` (explicit override; same manifest quirk as above — `required_services` is `{image_generator, object_store}`, not `llm`)
- **What it does**: Generates one image from a text prompt via an approved OpenAI image model, stores the bytes in object storage, and returns a reference (not raw bytes) plus generation metadata. Can be fully disabled via config (`backend: disabled`) so a workflow can toggle image generation off without removing the node.
- **Config fields**: `prompt` (required), `backend` (`disabled`\|`openai`, default `openai`), `image_model` (default `gpt-image-2-2026-04-21`; also `chatgpt-image-latest`, `dall-e-2`), `size` (default `auto`), `quality` (`auto`\|`low`\|`medium`\|`high`), `output_format` (`png`\|`jpeg`\|`webp`).
- **Produces (output)**: `generated`, `provider`, `model`, `minio_key` (`workflows/<run_id>/images/<node_id>.<ext>`), `content_type`, `byte_size`, `revised_prompt`.
- **Validation / gotchas**: When `backend: disabled`, requires no services at all. Returns a storage-key **reference**, not an inline data URI — feed it through `FigureEmbedder`/`DynamicFigureAgent` before a document renderer if you need it inlined.
- **When to use it**: Generating exactly one image from a known prompt, when only a stored reference is needed (not embedded into a document directly).

#### DynamicFigureAgent
- **Category / execution_kind**: Multimodal / `deterministic` (per the manifest's mechanical computation — `required_services` isn't overridden, so preflight won't flag a missing image-generation service for this node even though it needs one at runtime)
- **What it does**: Scans final drafted content for every `[[IMAGE PROMPT: <prompt>]]` marker, generates a diagram-styled image per marker (bounded by `max_images`), base64-encodes each into an inline data URI, and emits two parallel content variants: `illustrated_content` (markers replaced with `<figure><img data-uri>` for the DOCX/illustrated renderer) and `captioned_content` (markers replaced with plain `*Figure N: <prompt>*` text for text-only renderers) — so a raw, unresolved marker never leaks into either output.
- **Config fields**: `content` (required — should be the *final* text, after all rewrites), `image_model` (default `gpt-image-2-2026-04-21`), `size`/`quality`/`output_format`, `max_images` (default 10, ≤10), `fail_open` (default `true` — degrades to captions instead of hard-failing if image generation is unavailable).
- **Produces (output)**: `illustrated_content`, `captioned_content`, `figures` (`{index, prompt, generated, minio_key, byte_size, error}` per marker), `markers_found`, `images_generated`.
- **Validation / gotchas**: Each marker's generation is individually try/excepted — one failure doesn't abort the others. Markers beyond `max_images` always degrade to captions even in `illustrated_content`.
- **When to use it**: The modern, single-node replacement for the older fixed-image-nodes-plus-`FigureEmbedder` pattern, whenever a drafting step itself emits `[[IMAGE PROMPT: ...]]` markers.

#### FigureEmbedder
- **Category / execution_kind**: Multimodal / `deterministic`
- **What it does**: Replaces `[[IMAGE PROMPT: marker]]` placeholders with real embedded images read from object storage, for the older pattern where separate, explicitly-configured image-generation nodes exist (one per named figure). The DOCX renderer only ever embeds a picture from an inline `data:image/...;base64,...` URI already present in its content — since an LLM can't reliably paste a multi-KB base64 blob into its own prose, this node performs that substitution deterministically.
- **Config fields**: `content` (required), `figures` (list of `{marker, image (the whole upstream image-generation node's output object, e.g. `{{inputs.figure}}`), alt_text}`).
- **Produces (output)**: `content` (markers replaced with `![alt_text](data:...)` markdown), `embedded_count`, `unmatched_figures` (markers never found in content — usually the drafter paraphrased instead of copying verbatim), `missing_images` (marker found but image unavailable — left in place to fall back to the renderer's normal placeholder box).
- **Validation / gotchas**: A marker must be reproduced **verbatim** (case-insensitive) in `content` or it lands in `unmatched_figures`. Requires `object_store` only if `figures` is non-empty.
- **When to use it**: The manual/explicit pattern — prefer `DynamicFigureAgent` for workflows where the drafter itself emits an arbitrary number of markers.

### 8.7 Document Rendering & Export

Reading and producing office documents.

#### ExcelTableExtractor
- **Category / execution_kind**: Document Rendering & Export / `input`
- **What it does**: Extracts every sheet of an `.xlsx` in object storage into plain rows/cells.
- **Config fields**: `minio_key` (required).
- **Produces (output)**: `tables` (`{sheet_name: [[cell, ...], ...]}`), `sheet_count`, `total_rows`.

#### PDFTextExtractor
- **Category / execution_kind**: Document Rendering & Export / `input`
- **What it does**: Extracts per-page text from a `.pdf` in object storage. No OCR — a scanned/image-only PDF yields little or no text.
- **Config fields**: `minio_key` (required).
- **Produces (output)**: `pages` (per-page text dicts), `page_count`.

#### PDFProposalRenderer
- **Category / execution_kind**: Document Rendering & Export / `output`
- **What it does**: The flagship PDF deliverable renderer — a map of `section_name → section_text` into a styled, print-ready PDF (WeasyPrint), stored in object storage.
- **Config fields**: `sections` (`dict[str,str]`, required), `template` (`corporate`\|`professional`\|`warm`, default `corporate`), `proposal_title` (required), `client_name` (required).
- **Produces (output)**: `minio_key` (`workflows/<run_id>/proposal.pdf`), `byte_size`, `template_used`.
- **Gotchas**: Does not itself check for unresolved `{{...}}` markers in `sections` the way the Horizon renderers do — a broken template renders literally into the PDF.

#### PowerPointProposalSlides
- **Category / execution_kind**: Document Rendering & Export / `output`
- **What it does**: One slide per section plus a title slide, from the same `sections` shape.
- **Config fields**: `sections` (required), `proposal_title` (required), `client_name` (required).
- **Produces (output)**: `minio_key` (`.pptx`), `slide_count` (`len(sections) + 1` — a simple count, not the true rendered slide count if a section spans multiple slides internally), `byte_size`.

#### DOCXProposalRenderer
- **Category / execution_kind**: Document Rendering & Export / `output`
- **What it does**: The `.docx` counterpart to `PDFProposalRenderer` — same `sections`/`template`/`proposal_title`/`client_name` shape, drop-in interchangeable. Understands a specific Markdown subset (headings, bullet/numbered lists, pipe tables with a `---` separator row, `**bold**`/`*italic*`/`` `code` ``) — anything more exotic renders as plain text.
- **Config fields**: same as `PDFProposalRenderer`.
- **Produces (output)**: `minio_key` (`.docx`), `byte_size`, `template_used`.
- **Gotchas**: A Markdown table needs its dashed separator row or the renderer won't detect it as a table at all.

#### HorizonDOCXProposalRenderer
- **Category / execution_kind**: Document Rendering & Export / `output`
- **What it does**: A domain-specific Horizon Europe Part B renderer — converts a single Markdown/HTML content blob plus metadata/citations/evidence data into a submission-styled `.docx` with native TOC, headings, tables, figures, page-number fields, a PDF-equivalent page-count estimate, and an optional evidence annex.
- **Config fields**: `content` (required), `content_format` (`markdown`\|`html`, default `markdown`), `metadata`/`citation_registry`/`evidence_qa`/`evidence_blockers` (each must resolve from a template to a real dict/list — a leftover `str` at runtime is a hard error naming the unresolved field), `include_toc`/`include_bibliography`/`include_evidence_annex`, `page_limit` (default 45), `enforce_page_limit` (default `false` — otherwise only a warning), `enable_footnotes`, `max_content_characters` (default 2,000,000), `max_embedded_image_bytes`.
- **Produces (output)**: `minio_key`/`docx_key`, `source_html_key`, `byte_size`, `estimated_page_count` (a PDF-equivalent-layout **estimate**, not literal Word pagination — see `page_count_basis`), `docx_sha256`, `table_of_contents`, `warnings`, `submission_ready` (only `true` if no evidence blockers and no page-limit/INPUT-NEEDED warnings).
- **When to use it**: The final DOCX-producing step for a Horizon Part B submission needing citation/TOC/page-limit awareness — prefer this over the generic `DOCXProposalRenderer` for proposal-specific formatting.

#### HorizonHTMLProposalRenderer
*(registers as `HorizonHTMLProposalRenderer`, defined in `app/nodes/html_proposal_renderer.py`)*
- **Category / execution_kind**: Document Rendering & Export / `output`
- **What it does**: The PDF counterpart to the above — same Horizon Part B cover/TOC/citation/page-limit handling, but produces a print-ready PDF plus its underlying HTML.
- **Config fields**: same core fields as `HorizonDOCXProposalRenderer` minus `enable_footnotes`/`max_embedded_image_bytes` (DOCX-specific).
- **Produces (output)**: `minio_key`/`pdf_key`, `html_key`, `page_count` (here a **real measured** count from the generated PDF via `pdfplumber` — unlike the DOCX renderer's estimate, these can diverge slightly), `submission_ready`.
- **When to use it**: The final PDF-producing step, when a formatted, submission-ready PDF (plus HTML source) with hard page-limit enforcement is needed — use the DOCX variant instead when an editable Word file is required.

### 8.8 Integrations

External tool/skill protocols.

#### MCPAgent
- **Category / execution_kind / uses_ai / external_action**: Integrations / `external` / `true` / `true`
- **What it does**: An **autonomous agent loop** — fundamentally different from `MCPToolAgent`. It discovers every tool on the connected MCP server(s), hands the *entire* tool list to the model, and loops: on each turn the model itself decides whether to call a tool (and which, with what arguments) or to give a final answer. Every tool call the model chooses is executed for real. **There is no policy gate here, no read/write distinction, and no human-approval check** on which tools get invoked — treat any use of this node against write-capable tools as materially higher risk than the same tools reached via `MCPToolAgent`, since the calling decision itself is delegated to the model rather than authored and reviewed in the workflow's config.
- **Config fields**: `model` (default `claude-sonnet-4-5`), `objective` (required, templated), `system_prompt`, `max_iterations` (default 5), `temperature` (default 0.2).
- **Produces (output)**: `answer`, `tool_calls` (`{iteration, tool, arguments, result_preview}`), `iterations_used`, `completed` (`false` if it hit `max_iterations` without a final answer).
- **When to use it**: Open-ended, exploratory investigation where the model should genuinely choose which of several tools to use and in what order — e.g. read-only research over a document MCP server. Never for a specific, author-controlled, possibly write-capable action (use `MCPToolAgent` for that).

#### ScientificSkillAgent
- **Category / execution_kind / uses_ai / external_action**: Integrations / `external` (the category-table classification reflects imported skill content, not a live external call — this is a single-shot completion, no tool loop) / `true` / `true` (per the table)
- **What it does**: A single LLM completion for scientific writing/synthesis, augmented with guidance pulled from the platform's Scientific Agent Skill catalog (auto-selected against the objective, or explicit). The model is explicitly told the skill text is guidance only — never permission to run commands, call external services, or bypass RBAC/evidence/review controls; the user's objective outranks anything conflicting inside a skill document.
- **Config fields**: `model` (default `claude-sonnet-4-5`), `objective` (required), `skills` (explicit list, or empty for auto-select), `auto_select` (default `true`), `max_skills` (default 3), `system_prompt`, `temperature` (default 0.1), `max_tokens` (default 8192).
- **Produces (output)**: `answer`, `skills_used`, `skill_versions`, `selection_reason`.
- **When to use it**: One scientific-writing/synthesis step where the response should be shaped by curated, versioned skill guidance rather than a bare system prompt — pair with a retrieval/research node upstream if it needs facts not already in context, since this node is one-shot and never retrieves anything itself.

### 8.9 Uncategorized

#### KnowledgeRetrieval
> **Registry gap, worth knowing about**: this node's `type_name` (`"KnowledgeRetrieval"`) does not appear in the category table at all, so it falls back to the generic **"Other"** category in the Builder's palette — searching by "Evidence & Retrieval" will not surface it even though it's conceptually part of that family.

- **Category / execution_kind / uses_ai / external_action**: Other (uncategorized) / `deterministic` (inferred) / `false` / `false`
- **What it does**: Retrieves secured knowledge through a saved Retrieval Profile, **without generating an answer** — the retrieval-only complement to `RAGAgent`'s retrieval-plus-generation. Retrieval strategy (vector/BM25/hybrid, etc.) lives entirely in the saved Retrieval Profile, not in this node's config.
- **Config fields**: `collection_id` (required), `retrieval_profile_id` (required), `query` (required), `runtime_filters` (default `{}`).
- **Produces (output)**: `retrieved_chunks`, `citations` (each `evidence_status: "retrieved_not_verified"`), `context` (assembled text), `retrieval_trace_id`, `collection_id`/`resolved_index_id`/`retrieval_profile_id`, `candidate_count`/`context_count`, `timings_ms`.
- **Validation / gotchas**: Every citation is explicitly `"retrieved_not_verified"` — treat it exactly like any other candidate output elsewhere in the platform.
- **When to use it**: When you need retrieved chunks/citations/context as data for a downstream step (a custom prompt, a router, a verification node) without also generating a synthesized answer in the same step. Use `RAGAgent` instead if you want retrieval *and* generation together.

---

## 9. Quick-reference index

| Node type | Category | Execution kind | Uses AI | External action |
|---|---|---|---|---|
| WorkflowInputAgent | Core Building Blocks | input | no | no |
| AITaskAgent | Core Building Blocks | ai | yes | no |
| DecisionAgent | Core Building Blocks | deterministic | no | no |
| RouterAgent | Core Building Blocks | deterministic (ai in `llm` mode) | conditional | no |
| DataTransformAgent | Core Building Blocks | deterministic | no | no |
| HumanInLoopAgent | Core Building Blocks | human | no | no |
| EmailAgent | Core Building Blocks | external | no | yes |
| MCPToolAgent | Core Building Blocks | external | no | yes |
| Literal | Control & Flow | input | no | no |
| Echo | Control & Flow | deterministic | no | no |
| TransformAgent | Control & Flow | ai | yes | no |
| TextAssemblerAgent | Control & Flow | deterministic | no | no |
| WorkflowFileLoader | Control & Flow | input | no | no |
| BoundedDeepResearchAgent | Research & Discovery | ai | yes | yes |
| WebSearchAgent | Research & Discovery | external | no | yes |
| ScholarlyCandidateDiscoveryAgent | Research & Discovery | external | yes | yes |
| ResearchSourceAcquirer | Research & Discovery | external | no | yes |
| ScientificResearchPlannerAgent | Research & Discovery | ai | yes | no |
| RAGAgent | Evidence & Retrieval | ai | yes | no |
| InternalProjectEvidenceRetrieverAgent | Evidence & Retrieval | ai | yes | no |
| PriorProjectRetrieverAgent | Evidence & Retrieval | deterministic | no | no |
| StructuredDatasetRetrieverAgent | Evidence & Retrieval | deterministic | no | no |
| PaperQAEvidenceSynthesizerAgent | Evidence & Retrieval | deterministic | no (undercounted) | no |
| MinIOEvidenceIngestion | Evidence & Retrieval | external | no | no |
| ClaimEvidenceVerifier | Evidence & Retrieval | ai | yes | no |
| ProposalTruthGraphAgent | Evidence & Retrieval | deterministic | no | no |
| CitationRegistryBuilder | Evidence & Retrieval | deterministic | no | no |
| GraphNormalizer | Proposal Engineering | deterministic (pinned) | yes | no |
| ConceptAlternativesAgent | Proposal Engineering | ai | yes | no |
| ConceptFreezeAgent | Proposal Engineering | deterministic | no | no |
| MethodologyEngineeringAgent | Proposal Engineering | ai | yes | no |
| ProposalEvidenceFactoryAgent | Proposal Engineering | ai | yes | no |
| ConsistencyChecker | Proposal Engineering | deterministic | no | no |
| CallCoverageMatrixAgent | Proposal Engineering | deterministic | no | no |
| HorizonEvaluationAgent | Proposal Engineering | ai | yes | no |
| ProposalSubmissionGate | Proposal Engineering | deterministic | no | no |
| KimiVisionAgent | Multimodal | ai | yes (undercounted as no) | no |
| OpenAIImageGenerationAgent | Multimodal | ai | yes (undercounted as no) | no |
| DynamicFigureAgent | Multimodal | deterministic (undercounted) | no | no |
| FigureEmbedder | Multimodal | deterministic | no | no |
| ExcelTableExtractor | Document Rendering & Export | input | no | no |
| PDFTextExtractor | Document Rendering & Export | input | no | no |
| PDFProposalRenderer | Document Rendering & Export | output | no | no |
| PowerPointProposalSlides | Document Rendering & Export | output | no | no |
| DOCXProposalRenderer | Document Rendering & Export | output | no | no |
| HorizonDOCXProposalRenderer | Document Rendering & Export | output | no | no |
| HorizonHTMLProposalRenderer | Document Rendering & Export | output | no | no |
| MCPAgent | Integrations | external | yes | yes |
| ScientificSkillAgent | Integrations | external | yes | yes |
| KnowledgeRetrieval | Other (uncategorized) | deterministic | no | no |

*"Undercounted" flags a node whose manifest `uses_ai`/`execution_kind` badge is computed from `required_services({})` and doesn't reflect a real internal model call the node makes through a different service name (e.g. `kimi_vision`, `image_generator`, or PaperQA2's own internal LLM config, rather than the shared `llm` service). Treat these nodes as AI-using in practice regardless of the badge.*
