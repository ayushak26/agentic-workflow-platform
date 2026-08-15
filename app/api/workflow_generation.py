"""Auto-create a workflow from a natural-language prompt, tested before it's
handed back.

No sandboxed dry-run execution exists anywhere in this codebase (confirmed
during investigation — app/runtime/preflight.py's `_compile_dry_run` only
compiles the LangGraph, it never runs a node). "Rigorous testing" here means
two real stages: (1) a static preflight/connectivity repair loop, cheap and
side-effect-free, run every attempt; then (2) exactly one genuine end-to-end
execution via app.runtime.executor.run_workflow (real LLM/API calls) once
static checks are clean, with up to two more regenerate-and-retest rounds if
that real run fails. This mirrors the platform's normal run path but is
intentionally NOT persisted to run_history/audit — it's a disposable test
execution, not a production run.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.nodes.registry import NodeRegistry
from app.runtime.autofix import apply_deterministic_fixes, format_preflight_feedback
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import WorkflowPreflightReport, preflight_workflow_for_run
from app.runtime.schema import WorkflowInputSpec
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.capability_selection import (
    GENERATION_MODEL_COMPLEX,
    GENERATION_MODEL_COMPLEX_REASONING_EFFORT,
    GENERATION_MODEL_SIMPLE,
    select_candidate_node_types,
    select_generation_model,
)
from app.workflow.preflight_stats import record_attempt

router = APIRouter(prefix="/api/workflows", tags=["workflow-generation"])

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Routed through OpenRouter (app/llm/openrouter_gw.py, via the "openrouter/"
# prefix in app/llm/registry.py's _PREFIX_ROUTES) rather than calling
# Anthropic directly — generation is high-volume/retried and shouldn't be
# hostage to one provider account's balance. Which of the two tiers a given
# request actually uses is chosen automatically by
# app.workflow.capability_selection.select_generation_model, based on the
# same node-type shortlist step 1 already computed — see
# build_llm_yaml_generator below. GENERATION_MODEL itself is kept as the
# simple-tier alias: the default when nothing calls the auto-selector
# directly (e.g. a future caller that just wants "a reasonable model").
GENERATION_MODEL = GENERATION_MODEL_SIMPLE
MAX_STATIC_ATTEMPTS = 4
MAX_REAL_EXECUTION_ATTEMPTS = 1 + 2  # one real run, then up to 2 repair-and-retry rounds

_EXAMPLE_WORKFLOW_YAML = """
name: Hello Workflow
description: >-
  Literal feeds a template, an LLM summarizes it into a declared structured
  field, and a final node reads both the structured field and the raw text.
version: "1.0"
use_case: generic
inputs:
  who:
    type: text
nodes:
  - id: first
    type: Literal
    config:
      value: world
  - id: second
    type: Echo
    config:
      template: "Hello, {{first.value}}!"
  - id: extract
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "Summarize this greeting in one word: {{second.text}}"
      output_schema:
        summary: str
  - id: render
    type: Echo
    config:
      template: "Summary: {{extract.parsed.summary}} (raw model text: {{extract.raw}})"
edges:
  - from: first
    to: second
  - from: second
    to: extract
  - from: extract
    to: render
entry: first
exit: render
"""

_ROUTING_EXAMPLE_YAML = """
name: Routing Example
description: >-
  A RouterAgent branches on a classified field; each branch ends in its own
  exit instead of reconverging into one shared downstream node.
version: "1.0"
inputs:
  message:
    type: text
    required: true
nodes:
  - id: classify
    type: AITaskAgent
    config:
      task: classify
      model: auto
      input: "{{inputs.message}}"
      output_fields:
        - name: category
          type: enum
          enum_values: [sales, support]
          required: true
  - id: route_by_category
    type: RouterAgent
    config:
      mode: field
      route_field: classify.result.category
      branches:
        sales: to_sales
        support: to_support
      fallback: to_support
  - id: to_sales
    type: Echo
    config:
      template: "Routed to Sales: {{inputs.message}}"
  - id: to_support
    type: Echo
    config:
      template: "Routed to Support: {{inputs.message}}"
edges:
  - from: classify
    to: route_by_category
  - from: route_by_category
    condition: route
    branches:
      to_sales: to_sales
      to_support: to_support
entry: classify
exit:
  - to_sales
  - to_support
"""

_SYSTEM_PROMPT_TEMPLATE = """You generate workflow YAML files for an agentic workflow builder. \
Work through these three steps in order — do not jump to writing YAML before \
finishing steps 1 and 2.

STEP 1 — REQUIRED NODE TYPES (already analysed for you): the request has \
already been analysed, deterministically, for which capabilities it needs. \
NODE TYPE CATALOG below is not this platform's full node library — it is the \
result of that analysis, a shortlist of exactly the node types relevant to \
this request. Every node's "type" you write MUST come from this list; never \
invent one and never reach for a type outside it because it "seems close \
enough" — if truly nothing here fits, prefer a Core Building Blocks type \
(AITaskAgent, DataTransformAgent, DecisionAgent, RouterAgent) over inventing \
a type name.

STEP 2 — STUDY REAL USAGE: REAL USAGE EXAMPLES below shows, for each \
candidate type that has one, a node of that exact type exactly as it is \
configured in a real, already-working workflow on this platform — not a \
paraphrase. Before writing a node of a given type, read its real example \
closely: which fields it actually sets, how it templates upstream values, \
how any nested structure is filled in. Match that shape. Where a type has no \
real example listed, its NODE TYPE CATALOG entry (field names and types) \
plus the FIELDSPEC SHAPE / field-type rules below are what you have instead.

STEP 3 — ASSEMBLE THE WORKFLOW: once you know the types (step 1) and how \
each is really configured (step 2), wire them into a complete workflow — \
edges, entry, exit — following the same configuration conventions the \
examples showed, and the WORKFLOW YAML SHAPE below.

A template like {{{{node_id.field}}}} may ONLY reference a field name that is \
listed under that node's "Output fields" below — never guess or invent one. \
Some node types (marked "declares structured output") have an output field \
that is a free-form dict shaped by that SAME node's own config.output_schema \
mapping: the dict is empty unless you declare output_schema on that node, and \
its only valid keys are exactly the ones you declared there (e.g. declaring \
`output_schema: {{summary: str}}` on a node makes `{{{{that_node.parsed.summary}}}}` \
valid, and nothing else under `.parsed` is). See the worked example below.

Every config field below is annotated with its exact type — treat that as a \
hard contract, not a suggestion. A field typed "string" accepts ONE string \
value, never an object: if you need to combine several labelled upstream \
values into one string field, write them into a single templated string \
yourself (e.g. a multi-line string with one "label: {{{{node_id.field}}}}" per \
line), do not turn the field into a dict of sub-keys. A field typed \
"object (label → value)" is the one place a dict of named entries belongs — \
e.g. on AITaskAgent, `input` is the single string the task reads and \
`context` is where several extra labelled sources go, they are not \
interchangeable.

FIELDSPEC SHAPE — a field marked "array of FieldSpec rows" (e.g. AITaskAgent's \
`output_fields`, WorkflowInputAgent's `fields`) is a list where each row has: \
`name` (required), `type` (one of exactly: string, text, number, integer, \
boolean, enum, object, list, date — never "array"), `description`, `required` \
(bool), and only when relevant: `enum_values` (required when type is "enum"), \
`fields` (a nested list of rows, required when type is "object", AND required \
when type is "list" with `item_type: object`), `item_type` (REQUIRED when type \
is "list" — one of string/text/number/integer/boolean/enum/date/object; a list \
of objects is `type: list`, `item_type: object`, with the object's own columns \
under `fields`, not under the list row itself). Example — a list of structured \
objects:
    output_fields:
      - name: postings
        type: list
        item_type: object
        fields:
          - {{name: title, type: string, required: true}}
          - {{name: company, type: string, required: true}}
          - {{name: url, type: string, required: true}}

TEMPLATES NEVER LOOP — a {{{{node_id.field}}}} reference always substitutes ONE \
scalar value; there is no for-each/loop syntax anywhere in this templating \
language. If a field is a list of structured items (e.g. `postings`, a list \
of objects from an AITaskAgent's output_fields), you CANNOT write something \
like `{{{{extract.result.postings.items.title}}}}` inside an Echo/TransformAgent \
template to render one line per item — that does not exist and will fail. \
To turn a list of structured items into formatted text (a numbered list, a \
report section, etc.), give the WHOLE list as the `input` to another \
AITaskAgent (task: generate or draft_response) and instruct it, in plain \
language, how to format each item — the model writes the per-item formatting \
in prose, deterministic templating never iterates.

A BARE (whole-value) `{{{{node_id.field}}}}` — the ENTIRE config value is just \
that one reference, nothing else around it — preserves the referenced \
field's real type: a list stays a list, an object stays an object. If the \
target config field requires "string" (e.g. AITaskAgent's `input`, Echo's \
`template`) and the source field is a list or object (e.g. WebSearchAgent's \
`results`, an AITaskAgent output_fields list), a bare reference FAILS at \
runtime with a "should be a valid string" error, because the raw list/object \
is passed straight through, not text. Fix this by embedding it in a little \
surrounding text instead, e.g. `"Search results:\n{{{{search_jobs.results}}}}"` \
rather than `"{{{{search_jobs.results}}}}"` alone — embedding always renders the \
value as readable text, whatever its underlying type.

Workflow-level `inputs:` (the top-level block, addressed as {{{{inputs.name}}}}) \
only supports `type: text`, `type: json`, or `type: file` — never `number`, \
`integer`, or `boolean`. A numeric or boolean input is still `type: text` (the \
value arrives as a string; parse it where it's used, e.g. inside a \
DataTransformAgent `number` operation, if you need it as a real number). This \
is a different, smaller type set from a FieldSpec row's `type` above — do not \
confuse the two.

`required` on a workflow input DEFAULTS TO FALSE if you omit it — an easy way \
to silently break the workflow. If a `{{{{inputs.name}}}}` reference is the \
WHOLE value of a config field that itself requires a value (almost every \
plain `string` field — e.g. WebSearchAgent's `query`, AITaskAgent's `input`), \
that input MUST be declared `required: true`, or a run where it's left empty \
resolves that field to a bare `None` and fails at execution, not at review \
time. Set `required: true` on every input something in the workflow cannot \
function without.

CONDITIONAL ROUTING — a conditional edge (the one following a RouterAgent, or \
any node with a `route` output field) has EXACTLY two keys besides `from`: \
`condition: route` (that literal string, always — NEVER a template like \
`{{{{node_id.route}}}}`; the compiler reads the node's real `route` output \
itself, `condition` is just the marker that this edge is conditional) and \
`branches` (a plain `{{route_value: target_node_id}}` map — never combined \
with a plain `to:` on the same edge). See the CONDITIONAL ROUTING EXAMPLE \
below for the full pattern. After branches diverge, do NOT reconverge them \
into one shared downstream node via separate plain edges (one `to:` per \
branch, all pointing at the same node) — the compiler treats more than one \
plain incoming edge as an AND-join that waits for every predecessor to run, \
but only ONE branch ever runs per request, so the shared node would wait \
forever for the others (FANIN_UNREACHABLE_ANDJOIN). Instead, either give each \
branch its own terminal node and list all of them in `exit:`, or have each \
branch do its own distinct processing rather than funneling back together. \
A router with N branches gets exactly ONE outgoing edge (the conditional one \
with `branches` mapping all N routes) — never N separate plain `to:` edges \
out of the router; that draws every branch as always running, which is not \
routing at all.

ROUTING/RULE FIELD PATHS ARE BARE, NOT TEMPLATES — RouterAgent's `route_field` \
and every rule/condition's `field` key (DecisionAgent's `rules[].when`, \
RouterAgent's `cases[].when`) are a plain dotted path string, e.g. \
`classify.result.category` — NEVER wrapped in `{{{{...}}}}` braces. These are \
evaluated by the rules engine directly, not substituted by the templating \
engine; writing `route_field: "{{{{classify.result.category}}}}"` (with braces) \
fails with UNKNOWN_FIELD_REFERENCE, because the literal string `{{{{...}}}}` \
is not itself a valid field path. Braces belong on ordinary `{{{{...}}}}` config \
fields (AITaskAgent's `input`, Echo's `template`, and similar) — never on a \
`route_field` or a rule's `field`.

NODE TYPE CATALOG (step 1's result):
{catalog}

REAL USAGE EXAMPLES (step 2 — study these before writing the equivalent node):
{examples}

WORKFLOW YAML SHAPE (required fields: name, nodes; nodes must be a non-empty \
list with unique ids; edges/entry/exit/inputs are optional but should \
normally be set for a real workflow):

{example}

CONDITIONAL ROUTING EXAMPLE (branches never reconverge — each has its own exit):

{routing_example}

Return ONLY the workflow YAML, with no markdown code fence and no \
commentary before or after it.
"""


_REPAIR_SYSTEM_PROMPT_TEMPLATE = """You repair existing workflow YAML documents for an agentic \
workflow builder. You are given a COMPLETE, ALREADY-EXISTING workflow definition that fails \
static validation, plus the exact list of validation errors it currently has. Your only job is \
to return that SAME document with the smallest set of edits needed to fix exactly those errors.

Preserve the workflow's name, description, node ids, node responsibilities, topology (edges, \
entry, exit), and every input/output contract whenever at all possible. Do NOT design a new \
workflow and do NOT change what the workflow does. Critically: the validation-errors text below \
is a list of defects in the document that follows it, NOT a natural-language request to build a \
workflow — if you ever find yourself writing a workflow whose own job is "analyze/repair workflow \
YAML," you have misunderstood the task. You are the one doing the repairing, directly on the \
document given to you; you are not building a tool that does it.

Hard rules for this platform's workflow YAML — violating any of these is exactly the kind of \
defect you are being asked to fix:
- A template like {{{{node_id.field}}}} may ONLY reference a field actually declared as an output \
field of that node — never invent or guess one.
- A FieldSpec row (e.g. AITaskAgent's output_fields) has name, type (string, text, number, \
integer, boolean, enum, object, list, date — never "array"), description, required, and only when \
relevant enum_values (type: enum) / fields (nested rows, type: object or type: list with \
item_type: object) / item_type (required when type: list).
- Templates never loop: a list of structured items cannot be rendered item-by-item via a template \
reference; that requires an AITaskAgent/DataTransformAgent instructed in plain language to format \
each item.
- A bare (whole-value) {{{{node_id.field}}}} preserves the referenced field's real type (list stays \
a list, object stays an object); embedding it inside surrounding text always renders it as a \
string — a plain string field (e.g. AITaskAgent's input, Echo's template) fails if given a list or \
object directly.
- Workflow-level inputs: only support type: text, type: json, or type: file, and required defaults \
to false if omitted.
- A conditional edge (after a RouterAgent or any node with a route output) has exactly from, \
condition: route (always that literal string, never a template), and branches (a plain \
{{{{route_value: target_node_id}}}} map) — never combined with a plain to: on the same edge, and \
branches must never reconverge into one shared downstream node via separate plain edges.
- route_field and any rule's field (DecisionAgent, RouterAgent cases[].when) are bare dotted \
paths, never wrapped in {{{{...}}}}.

NODE TYPE CATALOG (every node type already used in the document below is valid; consult this only \
if you need to understand a field you're changing):
{catalog}

Return ONLY the complete corrected workflow YAML, with no markdown code fence and no commentary \
before or after it."""


def _field_type_label(schema: dict) -> str:
    """A short type descriptor for one JSON-schema property, e.g. "string",
    "object (label → value)", "string enum". Shown next to every config
    field in the catalog so the model can't confuse a field that takes one
    string with one that takes a labelled group of values — see
    `_fix_dict_where_string_expected` in app/runtime/autofix.py for the
    real, observed failure mode this is meant to prevent (the model gave
    AITaskAgent's single-string `input` field a dict of several named
    upstream sources, which is what `context` is for)."""
    if not isinstance(schema, dict):
        return "any"
    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list):
        non_null = [v for v in variants if isinstance(v, dict) and v.get("type") != "null"]
        optional = len(non_null) != len(variants)
        label = _field_type_label(non_null[0]) if len(non_null) == 1 else "any"
        return f"{label} (optional)" if optional else label
    kind = schema.get("type")
    if kind == "string":
        return "string enum" if "enum" in schema else "string"
    if kind == "object":
        return "object (label → value)"
    if kind == "array":
        items = schema.get("items")
        if isinstance(items, dict) and str(items.get("$ref", "")).endswith("/FieldSpec"):
            return "array of FieldSpec rows — see FIELDSPEC SHAPE below"
        return "array"
    if kind in ("integer", "number"):
        return "number"
    if kind == "boolean":
        return "boolean"
    if "$ref" in schema or "allOf" in schema:
        return "object"
    return kind or "any"


def _node_type_catalog(type_names: list[str] | None = None) -> str:
    """The catalog text embedded in the generation system prompt. With
    `type_names` given, only those entries are described — see
    app.workflow.capability_selection, which picks a request-relevant
    shortlist so a generation call doesn't need to spend tokens describing
    every one of the ~49 registered node types. `None` (the default) keeps
    the full catalog, which is what every other caller of this function
    still wants (the node-types-chat "browse everything" case, and the
    existing test of this function)."""
    manifest = NodeRegistry.manifest()
    if type_names is not None:
        wanted = set(type_names)
        manifest = [entry for entry in manifest if entry["type_name"] in wanted]
    lines = []
    for entry in manifest:
        properties = (entry.get("config_schema") or {}).get("properties", {})
        config_fields = list(properties.keys())
        config_fields_typed = [
            f"{name} ({_field_type_label(schema)})" for name, schema in properties.items()
        ]
        output_fields = list((entry.get("output_schema") or {}).get("properties", {}).keys())
        structured = "output_schema" in config_fields
        line = (
            f"- {entry['type_name']} (category: {entry.get('category', 'Other')}): "
            f"{entry.get('description') or 'no description'}. "
            f"Config fields: {', '.join(config_fields_typed) or 'none'}. "
            f"Output fields: {', '.join(output_fields) or 'none'}"
            f"{' (declares structured output — see note above)' if structured else ''}."
        )
        lines.append(line)
    return "\n".join(lines)


# A real production workflow's AITaskAgent/DecisionAgent/RouterAgent node can
# run to hundreds of lines (a 30-field extraction schema, a dozen business
# rules) — genuinely useful to look at, but showing it in full on every
# generation call would spend far more tokens than step 2 is worth. Truncate
# at a clean line boundary instead of dropping the example entirely: the
# shape (which fields exist, how templating looks) is usually clear well
# before the cap.
_MAX_SNIPPET_CHARS = 700


def _real_usage_snippet(type_name: str, manifest_entry: dict) -> str | None:
    """Step 2's grounding for one node type: its own config, exactly as
    filled in by a real, already-working workflow on this platform, if one
    exists. Reuses `about_synthesis.py`'s adjacency-mined
    `example_workflow_path` (the same file the About tab already links to)
    rather than a second hand-maintained list of examples — the point is
    that this is a real file on disk, not a fabricated one, so degrades to
    `None` (no example) rather than inventing anything when there isn't one."""
    path = (manifest_entry.get("about") or {}).get("example_workflow_path")
    if not path:
        return None
    try:
        doc = yaml.safe_load((_REPO_ROOT / path).read_text())
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    for node in doc.get("nodes") or []:
        if not (isinstance(node, dict) and node.get("type") == type_name):
            continue
        snippet = {key: node[key] for key in ("id", "type", "config") if key in node}
        if not snippet:
            return None
        try:
            dumped = yaml.safe_dump(
                snippet, sort_keys=False, default_flow_style=False, allow_unicode=True,
            ).strip()
        except Exception:
            return None
        if len(dumped) <= _MAX_SNIPPET_CHARS:
            return dumped
        truncated = dumped[:_MAX_SNIPPET_CHARS].rsplit("\n", 1)[0]
        return f"{truncated}\n... (truncated — real file has more; shape shown is representative)"
    return None


def _real_usage_examples(type_names: list[str], manifest: list[dict]) -> str:
    """Step 2's text block: one real config snippet per candidate type that
    has one (see `_real_usage_snippet`). Deliberately scoped to the
    request's own shortlist, not the full registry — this is grounding for
    THIS generation call, not a reference manual."""
    by_name = {entry["type_name"]: entry for entry in manifest}
    blocks = []
    for type_name in type_names:
        entry = by_name.get(type_name)
        if entry is None:
            continue
        snippet = _real_usage_snippet(type_name, entry)
        if snippet:
            path = entry["about"]["example_workflow_path"]
            blocks.append(f"{type_name} (from {path}):\n{snippet}")
    if not blocks:
        return (
            "(No real-workflow example is on file yet for these node types — "
            "rely on the NODE TYPE CATALOG and the FIELDSPEC SHAPE / field-type "
            "rules above instead.)"
        )
    return "\n\n".join(blocks)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop the opening fence (optionally with a language tag) and the
        # closing fence if present.
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _synthesize_sample_inputs(
    specs: dict[str, WorkflowInputSpec], provided: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fill in placeholder values for a workflow's declared inputs so it can
    actually be run. Returns (inputs, None) on success, or (None, reason) if
    a required input can't be synthesized (only file inputs, which need a
    real uploaded reference this endpoint has no way to fabricate)."""
    provided = provided or {}
    inputs: dict[str, Any] = dict(provided)
    for name, spec in specs.items():
        if name in provided:
            continue
        if not spec.required:
            continue
        if spec.type == "text":
            inputs[name] = "Sample value for testing."
        elif spec.type == "json":
            inputs[name] = {}
        else:  # "file"
            return None, (
                f"Required file input {name!r} has no sample — skipping the "
                "real end-to-end execution stage (static checks still ran)."
            )
    return inputs, None


@dataclass
class GenerationAttempt:
    stage: str  # "static" | "real_execution"
    yaml: str
    success: bool
    detail: str


@dataclass
class GenerationResult:
    yaml: str
    success: bool
    preflight_report: WorkflowPreflightReport | None
    execution_result: dict[str, Any] | None
    execution_skipped_reason: str | None
    attempts: list[GenerationAttempt] = field(default_factory=list)


async def run_generation_pipeline(
    *,
    prompt: str,
    sample_inputs: dict[str, Any] | None,
    generate_yaml: Callable[[str, str | None, str | None], Awaitable[str]],
    static_check: Callable[[str], Awaitable[tuple[WorkflowPreflightReport, dict[str, WorkflowInputSpec]]]],
    execute: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> GenerationResult:
    """The repair-loop control flow, dependency-injected so it's testable
    without a real LLM/Mongo/executor (see tests/test_workflow_generation.py).

    generate_yaml(prompt, prior_yaml, feedback) -> new yaml text
    static_check(yaml) -> (WorkflowPreflightReport, declared workflow inputs)
    execute(yaml, inputs) -> {"status": "completed"|"paused"|"failed"|"rejected", "error": str|None, ...}
    """
    attempts: list[GenerationAttempt] = []
    current_yaml: str | None = None
    feedback: str | None = None
    report: WorkflowPreflightReport | None = None
    declared_inputs: dict[str, WorkflowInputSpec] = {}

    for _ in range(MAX_STATIC_ATTEMPTS):
        # Before burning an LLM attempt on a retry, try the free,
        # deterministic fixer on the previous attempt's report — it resolves
        # the mechanical cases (typo'd template field, unknown node type
        # with an obvious fuzzy match, etc.) without a model call. Only
        # applies from the second iteration on: the first attempt has no
        # prior yaml/report to fix yet.
        if current_yaml is not None and report is not None and not report.valid:
            fix_result = apply_deterministic_fixes(current_yaml, report)
            if fix_result.changed:
                current_yaml = fix_result.yaml_text
                report, declared_inputs = await static_check(current_yaml)
                if report.valid:
                    attempts.append(GenerationAttempt(
                        "static", current_yaml, True,
                        "Deterministic fix resolved remaining issues.",
                    ))
                    break
                issue_text = format_preflight_feedback(report)
                attempts.append(GenerationAttempt("static", current_yaml, False, issue_text))
                feedback = (
                    "Your previous YAML failed static validation with these "
                    f"issues: {issue_text}. Return a corrected, complete YAML."
                )

        current_yaml = await generate_yaml(prompt, current_yaml, feedback)
        report, declared_inputs = await static_check(current_yaml)
        if report.valid:
            attempts.append(GenerationAttempt("static", current_yaml, True, "Preflight passed."))
            break
        issue_text = format_preflight_feedback(report)
        attempts.append(GenerationAttempt("static", current_yaml, False, issue_text))
        feedback = (
            "Your previous YAML failed static validation with these "
            f"issues: {issue_text}. Return a corrected, complete YAML."
        )
    else:
        assert current_yaml is not None and report is not None
        return GenerationResult(
            yaml=current_yaml, success=False, preflight_report=report,
            execution_result=None, execution_skipped_reason=None, attempts=attempts,
        )

    assert current_yaml is not None and report is not None

    inputs, skip_reason = _synthesize_sample_inputs(declared_inputs, sample_inputs)
    if inputs is None:
        return GenerationResult(
            yaml=current_yaml, success=True, preflight_report=report,
            execution_result=None, execution_skipped_reason=skip_reason, attempts=attempts,
        )

    feedback = None
    for _ in range(MAX_REAL_EXECUTION_ATTEMPTS):
        result = await execute(current_yaml, inputs)
        if result.get("status") in ("completed", "paused"):
            attempts.append(GenerationAttempt("real_execution", current_yaml, True, str(result.get("status"))))
            return GenerationResult(
                yaml=current_yaml, success=True, preflight_report=report,
                execution_result=result, execution_skipped_reason=None, attempts=attempts,
            )

        error = str(result.get("error") or f"run ended with status {result.get('status')}")
        attempts.append(GenerationAttempt("real_execution", current_yaml, False, error))
        feedback = (
            f"Your previous YAML passed static checks but FAILED when actually "
            f"run, with this error: {error}. Return a corrected, complete YAML "
            "that fixes this."
        )
        # Regenerate and re-validate statically before the next real attempt —
        # a repaired workflow must still pass structural checks first.
        current_yaml = await generate_yaml(prompt, current_yaml, feedback)
        report, declared_inputs = await static_check(current_yaml)
        if report.valid:
            new_inputs, skip_reason = _synthesize_sample_inputs(declared_inputs, sample_inputs)
            if new_inputs is not None:
                inputs = new_inputs
        if not report.valid:
            issue_text = format_preflight_feedback(report)
            attempts.append(GenerationAttempt("static", current_yaml, False, issue_text))
            return GenerationResult(
                yaml=current_yaml, success=False, preflight_report=report,
                execution_result=result, execution_skipped_reason=None, attempts=attempts,
            )

    return GenerationResult(
        yaml=current_yaml, success=False, preflight_report=report,
        execution_result=result, execution_skipped_reason=None, attempts=attempts,
    )


class GenerateWorkflowRequest(BaseModel):
    prompt: str
    sample_inputs: dict[str, Any] | None = None


def build_llm_yaml_generator(
    llm: Any, services: dict[str, Any], scope: str, *, mode: str = "generate",
) -> Callable[[str, str | None, str | None], Awaitable[str]]:
    """Build a `generate_yaml(base_prompt, prior_yaml, feedback) -> yaml text`
    closure over a live LLM client.

    `mode="generate"` (the default, used by /generate) frames the call as
    "describe a new workflow from this natural-language request." `mode="repair"`
    (used by /workflows/autofix) frames it instead as "edit this existing
    workflow document to fix these specific errors" — a deliberately different
    system prompt and user-prompt shape (see `_REPAIR_SYSTEM_PROMPT_TEMPLATE`),
    not a repurposing of the generate-mode prompt with a terse `base_prompt`.
    That repurposing was the actual root cause of a real incident: fed a
    one-line repair instruction through the "describe a workflow to build"
    slot, the model read it as a literal spec and built a workflow whose job
    was "repair workflow YAML" instead of returning the user's own workflow
    with fixes applied — a well-formed but entirely wrong result that passed
    static validation and silently overwrote the user's canvas.

    The rest of this docstring (shortlist/model-tier escalation) describes
    `mode="generate"` only — `mode="repair"` always uses the full node
    catalog and the complex model tier from the first call; a document that
    already failed static validation once deserves the more careful
    treatment from the start, and the request itself (the fixed sentence in
    `app.runtime.autofix.GENERIC_REPAIR_PROMPT`) carries no request-specific
    signal a shortlist heuristic could use anyway.

    The very first call describes only a request-relevant shortlist of node
    types (app.workflow.capability_selection) rather than the full registry —
    most requests only need a handful of the ~49 registered types, and
    describing all of them on every call spends tokens the model doesn't
    need. Any retry after a failure (static or real-execution) escalates to
    the full catalog for the rest of this closure's life: a repair attempt
    must never be worse-informed than before this change, and the full
    catalog is the safety net for the (rarer) case where the shortlist
    missed a type the workflow actually needed.

    The model itself is also chosen automatically, per request, from the
    same shortlist (`select_generation_model`): a request that only needs
    Core Building Blocks-shaped node types runs on the simple tier; one
    touching several specialized capabilities (or the platform's heaviest
    structured-reasoning categories — Proposal Engineering, Evidence &
    Retrieval) runs on the complex tier, at that model's highest reasoning
    effort. A retry after a failure always escalates straight to the complex
    tier, on the same reasoning as the catalog escalation above: a repair
    attempt deserves the stronger model regardless of how simple the
    original request looked.
    """
    full_catalog = _node_type_catalog()
    system_prompt_state = {
        "escalated": mode == "repair",
        "model": GENERATION_MODEL_COMPLEX if mode == "repair" else GENERATION_MODEL_SIMPLE,
    }

    def _system_prompt(base_prompt: str) -> str:
        if mode == "repair":
            return _REPAIR_SYSTEM_PROMPT_TEMPLATE.format(catalog=full_catalog)
        if system_prompt_state["escalated"]:
            # The full registry is the safety net for a retry — but step 2's
            # real-usage grounding stays scoped to the original shortlist
            # (re-dumping an example for all ~50 types on every retry would
            # cost more tokens than the step is worth at that point).
            catalog = full_catalog
            examples = (
                "(Escalated to the full node type catalog after a failed "
                "attempt — rely on the catalog and shape rules above; step "
                "2's real-usage grounding applied only to the first attempt.)"
            )
        else:
            manifest = NodeRegistry.manifest()
            candidates = select_candidate_node_types(base_prompt, manifest)
            catalog = _node_type_catalog(candidates)
            examples = _real_usage_examples(candidates, manifest)
            system_prompt_state["model"] = select_generation_model(base_prompt, manifest)
        return _SYSTEM_PROMPT_TEMPLATE.format(
            catalog=catalog, examples=examples, example=_EXAMPLE_WORKFLOW_YAML,
            routing_example=_ROUTING_EXAMPLE_YAML,
        )

    async def generate_yaml(base_prompt: str, prior_yaml: str | None, feedback: str | None) -> str:
        if feedback and mode != "repair":
            # A retry means the shortlist may have been wrong (or simply
            # incomplete) — widen to the full registry starting with this
            # very call, not just subsequent ones. Same reasoning promotes
            # the model straight to the complex tier: whatever the original
            # complexity estimate said, an attempt that already failed once
            # deserves the stronger model for the rest of this generation.
            system_prompt_state["escalated"] = True
            system_prompt_state["model"] = GENERATION_MODEL_COMPLEX
        system_prompt = _system_prompt(base_prompt)
        model = system_prompt_state["model"]
        context_llm = llm.with_context(
            run_id="workflow-generation", session_id=scope, node_id="generate",
            ledger=services.get("cost_ledger"),
        ) if hasattr(llm, "with_context") else llm
        if mode == "repair":
            # `base_prompt` (GENERIC_REPAIR_PROMPT) is a fixed instruction,
            # not a description of a workflow to design — never fed through
            # the "Describe workflow: ..." slot generate-mode uses, which is
            # exactly what caused the model to build a workflow that
            # performs YAML repair instead of returning the user's own,
            # repaired workflow. The document to edit is the whole prompt.
            assert prior_yaml is not None, "repair mode always has an existing document to fix"
            user_prompt = f"Existing workflow YAML to repair:\n{prior_yaml}"
            if feedback:
                user_prompt += f"\n\n{feedback}"
        else:
            user_prompt = f"Describe workflow: {base_prompt}"
            if prior_yaml and feedback:
                user_prompt += f"\n\nPrevious attempt:\n{prior_yaml}\n\n{feedback}"
        # The complex tier's own reasoning-effort dial — a complex or
        # already-failed-once attempt gets this model's most deliberate
        # reasoning, not just its response.
        reasoning_kwargs = (
            {"reasoning_effort": GENERATION_MODEL_COMPLEX_REASONING_EFFORT}
            if model == GENERATION_MODEL_COMPLEX else {}
        )
        response = await context_llm.complete(
            model=model, system=system_prompt, user=user_prompt, temperature=0.2,
            max_tokens=4096, **reasoning_kwargs,
        )
        return _strip_code_fence(response.text)

    return generate_yaml


@router.post("/generate")
async def generate_workflow_endpoint(
    req: GenerateWorkflowRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    services = getattr(request.app.state, "services", {})
    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")

    scope = getattr(user, "session_id", None) or user.username
    generate_yaml = build_llm_yaml_generator(llm, services, scope)

    async def static_check(yaml_text: str) -> tuple[WorkflowPreflightReport, dict[str, WorkflowInputSpec]]:
        # `req.sample_inputs` is genuinely absent for the overwhelming
        # majority of "generate from a prompt" calls — no real inputs exist
        # yet, the user hasn't run this workflow, they're still generating
        # it. Passing `None` through (rather than coercing to `{}`) is load-
        # bearing: preflight's own `_validate_inputs` treats `None` as "skip
        # input-presence validation, this is a structural-only check" and an
        # explicit `{}` as "these ARE the real inputs, and none were given" —
        # the latter used to fail REQUIRED_INPUT_MISSING for every required
        # input on every generation call that didn't happen to also pass
        # sample_inputs, which is nearly all of them. `sample_inputs`, when a
        # caller does provide it, still gets checked for real below.
        report = await preflight_workflow_for_run(
            yaml_text, provided_inputs=req.sample_inputs, services=services,
            probe_services=True, require_run_history=False,
        )
        declared_inputs: dict[str, WorkflowInputSpec] = {}
        if report.valid:
            try:
                declared_inputs = load_workflow_from_string(yaml_text).inputs
            except Exception:
                pass
        return report, declared_inputs

    async def execute(yaml_text: str, inputs: dict[str, Any]) -> dict[str, Any]:
        spec = load_workflow_from_string(yaml_text)
        try:
            return await run_workflow(
                spec, inputs, session_id=f"workflow-gen:{scope}",
                services=services, run_id=str(uuid.uuid4()),
            )
        except Exception as exc:
            # run_generation_pipeline's contract for `execute` is
            # {"status": ..., "error": ...} — never a raised exception — so
            # its repair loop can feed the error back and retry. A resolved
            # config that's structurally valid but wrong at runtime (e.g. an
            # optional `{{...}}?` template that legitimately resolved to
            # None for a field a downstream node actually required) can slip
            # past structural preflight and raise here instead. Report it
            # like any other failed run rather than letting it escape and
            # lose an otherwise-valid YAML.
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    try:
        result = await run_generation_pipeline(
            prompt=req.prompt,
            sample_inputs=req.sample_inputs,
            generate_yaml=generate_yaml,
            static_check=static_check,
            execute=execute,
        )
    except Exception as exc:
        # An upstream LLM provider failure (rate limit, auth, insufficient
        # credit, timeout, ...) previously crashed this endpoint with a bare,
        # unhelpful 500 — none of the SDK/provider exceptions get translated
        # into one of app.llm.errors' provider-neutral types before reaching
        # here. Surface it as a clean, actionable 502 instead; a genuine bug
        # in the pipeline itself will still show up in the server logs.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Workflow generation failed: {exc}",
        ) from exc

    await record_attempt(
        services.get("audit_db"),
        source="generate",
        workflow_name=result.preflight_report.workflow_name if result.preflight_report else None,
        success=result.success,
        error_codes=(
            [issue.code for issue in result.preflight_report.errors]
            if result.preflight_report else []
        ),
        total_attempts=len(result.attempts),
    )

    return {
        "yaml": result.yaml,
        "success": result.success,
        "preflight_report": result.preflight_report.model_dump(mode="json") if result.preflight_report else None,
        "execution_result": (
            {"status": result.execution_result.get("status"), "error": result.execution_result.get("error")}
            if result.execution_result else None
        ),
        "execution_skipped_reason": result.execution_skipped_reason,
        "attempts": [
            {"stage": a.stage, "success": a.success, "detail": a.detail}
            for a in result.attempts
        ],
    }
