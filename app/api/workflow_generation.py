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
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.nodes.registry import NodeRegistry
from app.runtime.autofix import apply_deterministic_fixes, format_preflight_feedback
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import WorkflowPreflightReport, preflight_workflow_for_run
from app.runtime.schema import WorkflowInputSpec
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.capability_selection import select_candidate_node_types
from app.workflow.preflight_stats import record_attempt

router = APIRouter(prefix="/api/workflows", tags=["workflow-generation"])

GENERATION_MODEL = "claude-opus-5"
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

_SYSTEM_PROMPT_TEMPLATE = """You generate workflow YAML files for an agentic workflow builder.

Every node's "type" MUST be one of the node types in this NODE TYPE CATALOG \
— it is generated live from the platform's current registry, so it is \
always complete and current. Never invent a node type that isn't listed.

A template like {{{{node_id.field}}}} may ONLY reference a field name that is \
listed under that node's "Output fields" below — never guess or invent one. \
Some node types (marked "declares structured output") have an output field \
that is a free-form dict shaped by that SAME node's own config.output_schema \
mapping: the dict is empty unless you declare output_schema on that node, and \
its only valid keys are exactly the ones you declared there (e.g. declaring \
`output_schema: {{summary: str}}` on a node makes `{{{{that_node.parsed.summary}}}}` \
valid, and nothing else under `.parsed` is). See the worked example below.

NODE TYPE CATALOG:
{catalog}

WORKFLOW YAML SHAPE (required fields: name, nodes; nodes must be a non-empty \
list with unique ids; edges/entry/exit/inputs are optional but should \
normally be set for a real workflow):

{example}

Return ONLY the workflow YAML, with no markdown code fence and no \
commentary before or after it.
"""


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
        config_fields = list((entry.get("config_schema") or {}).get("properties", {}).keys())
        output_fields = list((entry.get("output_schema") or {}).get("properties", {}).keys())
        structured = "output_schema" in config_fields
        line = (
            f"- {entry['type_name']} (category: {entry.get('category', 'Other')}): "
            f"{entry.get('description') or 'no description'}. "
            f"Config fields: {', '.join(config_fields) or 'none'}. "
            f"Output fields: {', '.join(output_fields) or 'none'}"
            f"{' (declares structured output — see note above)' if structured else ''}."
        )
        lines.append(line)
    return "\n".join(lines)


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
    llm: Any, services: dict[str, Any], scope: str,
) -> Callable[[str, str | None, str | None], Awaitable[str]]:
    """Build a `generate_yaml(base_prompt, prior_yaml, feedback) -> yaml text`
    closure over a live LLM client. Shared by /generate (a fresh workflow
    from a prompt) and /workflows/autofix (repairing an existing one) — both
    need the same node-type-catalog system prompt and prior-attempt/feedback
    convention, just with a different `base_prompt`.

    The very first call describes only a request-relevant shortlist of node
    types (app.workflow.capability_selection) rather than the full registry —
    most requests only need a handful of the ~49 registered types, and
    describing all of them on every call spends tokens the model doesn't
    need. Any retry after a failure (static or real-execution) escalates to
    the full catalog for the rest of this closure's life: a repair attempt
    must never be worse-informed than before this change, and the full
    catalog is the safety net for the (rarer) case where the shortlist
    missed a type the workflow actually needed.
    """
    full_catalog = _node_type_catalog()
    system_prompt_state = {"escalated": False}

    def _system_prompt(base_prompt: str) -> str:
        if system_prompt_state["escalated"]:
            catalog = full_catalog
        else:
            candidates = select_candidate_node_types(base_prompt, NodeRegistry.manifest())
            catalog = _node_type_catalog(candidates)
        return _SYSTEM_PROMPT_TEMPLATE.format(catalog=catalog, example=_EXAMPLE_WORKFLOW_YAML)

    async def generate_yaml(base_prompt: str, prior_yaml: str | None, feedback: str | None) -> str:
        if feedback:
            # A retry means the shortlist may have been wrong (or simply
            # incomplete) — widen to the full registry starting with this
            # very call, not just subsequent ones.
            system_prompt_state["escalated"] = True
        system_prompt = _system_prompt(base_prompt)
        context_llm = llm.with_context(
            run_id="workflow-generation", session_id=scope, node_id="generate",
            ledger=services.get("cost_ledger"),
        ) if hasattr(llm, "with_context") else llm
        user_prompt = f"Describe workflow: {base_prompt}"
        if prior_yaml and feedback:
            user_prompt += f"\n\nPrevious attempt:\n{prior_yaml}\n\n{feedback}"
        response = await context_llm.complete(
            model=GENERATION_MODEL, system=system_prompt, user=user_prompt, temperature=0.2,
            max_tokens=4096,
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
        report = await preflight_workflow_for_run(
            yaml_text, provided_inputs=req.sample_inputs or {}, services=services,
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
        return await run_workflow(
            spec, inputs, session_id=f"workflow-gen:{scope}",
            services=services, run_id=str(uuid.uuid4()),
        )

    result = await run_generation_pipeline(
        prompt=req.prompt,
        sample_inputs=req.sample_inputs,
        generate_yaml=generate_yaml,
        static_check=static_check,
        execute=execute,
    )

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
