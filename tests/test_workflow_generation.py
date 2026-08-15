from __future__ import annotations

from types import SimpleNamespace

import app.nodes  # noqa: F401 - populates the registry via discovery
import pytest
from fastapi import HTTPException

from app.api.workflow_generation import (
    _EXAMPLE_WORKFLOW_YAML,
    _MAX_SNIPPET_CHARS,
    _node_type_catalog,
    _real_usage_examples,
    _real_usage_snippet,
    _REPAIR_SYSTEM_PROMPT_TEMPLATE,
    _ROUTING_EXAMPLE_YAML,
    _SYSTEM_PROMPT_TEMPLATE,
    build_llm_yaml_generator,
    GenerateWorkflowRequest,
    GENERATION_MODEL,
    MAX_REAL_EXECUTION_ATTEMPTS,
    MAX_STATIC_ATTEMPTS,
    generate_workflow_endpoint,
    run_generation_pipeline,
)
from app.llm.openrouter_catalog import OPENROUTER_MODEL_ID_PATTERN
from app.nodes.registry import NodeRegistry
from app.runtime.preflight import (
    PreflightIssue,
    PreflightSeverity,
    WorkflowPreflightReport,
    preflight_workflow_yaml,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role

USER = CurrentUser(username="user@example.com", role=Role.CONSULTANT, session_id=None)

VALID_REPORT = WorkflowPreflightReport(valid=True)


def _invalid_report(message: str) -> WorkflowPreflightReport:
    return WorkflowPreflightReport(
        valid=False,
        issues=[PreflightIssue(code="bad_node", severity=PreflightSeverity.ERROR, message=message)],
    )


def test_node_catalog_lists_output_fields_and_flags_structured_output():
    catalog = _node_type_catalog()

    transform_line = next(line for line in catalog.splitlines() if line.startswith("- TransformAgent "))
    assert "Output fields: raw, parsed" in transform_line
    assert "declares structured output" in transform_line

    echo_line = next(line for line in catalog.splitlines() if line.startswith("- Echo "))
    assert "Output fields: text" in echo_line
    assert "declares structured output" not in echo_line


def test_generation_model_is_routed_through_openrouter_not_a_direct_provider():
    """GENERATION_MODEL previously called Anthropic directly and went down
    with that one account's credit balance. Routing through OpenRouter
    (app/llm/registry.py's "openrouter/" prefix) isn't tied to a single
    provider account; assert both the prefix and that it's a real,
    well-formed "openrouter/<vendor>/<model>" id, not just a string that
    happens to start right."""
    assert GENERATION_MODEL.startswith("openrouter/")
    assert OPENROUTER_MODEL_ID_PATTERN.match(GENERATION_MODEL)


def test_system_prompt_states_the_three_step_process_in_order():
    """The prompt should walk the model through identify-types, then
    study-real-usage, then assemble — in that order, and reference the
    sections that back each step, rather than presenting everything flat."""
    prompt = _SYSTEM_PROMPT_TEMPLATE
    step_1 = prompt.index("STEP 1")
    step_2 = prompt.index("STEP 2")
    step_3 = prompt.index("STEP 3")
    # The section *headers* (as opposed to references to them in the step
    # prose above) are what step_3 points readers at next — search past it.
    catalog_section = prompt.index("NODE TYPE CATALOG (step 1's result)", step_3)
    examples_section = prompt.index("REAL USAGE EXAMPLES (step 2", catalog_section)
    assert step_1 < step_2 < step_3 < catalog_section < examples_section
    assert "{catalog}" in prompt
    assert "{examples}" in prompt


def test_real_usage_snippet_finds_a_real_config_for_a_type_with_a_known_example():
    manifest = {entry["type_name"]: entry for entry in NodeRegistry.manifest()}
    entry = manifest["HumanInLoopAgent"]
    snippet = _real_usage_snippet("HumanInLoopAgent", entry)
    assert snippet is not None
    assert "type: HumanInLoopAgent" in snippet


def test_real_usage_snippet_truncates_a_large_real_config():
    """AITaskAgent's real examples on disk (a 20+ field extraction schema)
    run well past _MAX_SNIPPET_CHARS — confirm it's actually capped rather
    than dumped in full, and that the cap lands cleanly with a visible
    truncation marker instead of a silently cut-off YAML document."""
    manifest = {entry["type_name"]: entry for entry in NodeRegistry.manifest()}
    entry = manifest["AITaskAgent"]
    snippet = _real_usage_snippet("AITaskAgent", entry)
    assert snippet is not None
    assert len(snippet) <= _MAX_SNIPPET_CHARS + len("\n... (truncated — real file has more; shape shown is representative)")
    assert snippet.endswith("(truncated — real file has more; shape shown is representative)")


def test_real_usage_snippet_handles_missing_or_broken_inputs_gracefully():
    # No example on file at all for this type.
    assert _real_usage_snippet("Literal", {"about": {}}) is None
    assert _real_usage_snippet("Literal", {}) is None

    # The recorded file doesn't exist on disk (moved/deleted since the
    # adjacency scan ran).
    assert _real_usage_snippet(
        "Literal", {"about": {"example_workflow_path": "workflows/does_not_exist.yaml"}},
    ) is None

    # The recorded file exists but the requested type isn't actually in it.
    assert _real_usage_snippet(
        "SomeTypeNotInThisFile",
        {"about": {"example_workflow_path": "workflows/test_fixtures/hello_workflow.yaml"}},
    ) is None


def test_real_usage_examples_falls_back_to_a_message_when_nothing_is_found():
    text = _real_usage_examples(["TotallyUnknownType"], NodeRegistry.manifest())
    assert "No real-workflow example is on file" in text

    # An empty shortlist degrades the same way rather than raising.
    assert "No real-workflow example is on file" in _real_usage_examples([], NodeRegistry.manifest())


def test_real_usage_examples_only_covers_the_given_shortlist():
    """Scoped to the request's own candidates, not every type that happens
    to have an example — otherwise it stops being a compact, request-scoped
    grounding step and becomes a second full catalog dump."""
    text = _real_usage_examples(["Literal"], NodeRegistry.manifest())
    assert "Literal (from" in text
    assert "AITaskAgent (from" not in text


def test_example_workflow_yaml_passes_structural_preflight():
    """The worked example in the generation system prompt is the model's
    only concrete template of a multi-node reference chain — if it doesn't
    pass preflight itself, it's actively teaching the bug it's meant to
    prevent."""
    report = preflight_workflow_yaml(_EXAMPLE_WORKFLOW_YAML)
    assert report.valid, [issue.message for issue in report.errors]


def test_routing_example_workflow_yaml_passes_structural_preflight():
    """The conditional-routing worked example is the model's only concrete
    template of `condition: route` + non-reconverging branches — if it
    doesn't pass preflight itself, it's teaching the exact bug (a templated
    `condition`, or branches funnelled back into one AND-join node) it
    exists to prevent."""
    report = preflight_workflow_yaml(_ROUTING_EXAMPLE_YAML)
    assert report.valid, [issue.message for issue in report.errors]


@pytest.mark.asyncio
async def test_success_on_first_try_with_no_declared_inputs():
    async def generate_yaml(prompt, prior_yaml, feedback):
        return "name: ok\nnodes: [{id: a, type: Literal, config: {value: 1}}]\n"

    async def static_check(yaml_text):
        return VALID_REPORT, {}

    calls = []

    async def execute(yaml_text, inputs):
        calls.append((yaml_text, inputs))
        return {"status": "completed"}

    result = await run_generation_pipeline(
        prompt="a trivial workflow", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is True
    assert len(calls) == 1
    assert len(result.attempts) == 2  # one static pass + one real execution pass


@pytest.mark.asyncio
async def test_static_failure_is_repaired_then_succeeds():
    attempt_count = 0

    async def generate_yaml(prompt, prior_yaml, feedback):
        nonlocal attempt_count
        attempt_count += 1
        return f"attempt-{attempt_count}"

    async def static_check(yaml_text):
        if yaml_text == "attempt-1":
            return _invalid_report("unknown node type"), {}
        return VALID_REPORT, {}

    async def execute(yaml_text, inputs):
        return {"status": "completed"}

    result = await run_generation_pipeline(
        prompt="p", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is True
    assert result.yaml == "attempt-2"
    assert result.attempts[0].stage == "static"
    assert result.attempts[0].success is False
    assert "unknown node type" in result.attempts[0].detail


@pytest.mark.asyncio
async def test_exhausts_static_attempts_and_returns_failure_without_ever_executing():
    async def generate_yaml(prompt, prior_yaml, feedback):
        return "always-broken"

    async def static_check(yaml_text):
        return _invalid_report("still broken"), {}

    executed = []

    async def execute(yaml_text, inputs):
        executed.append(yaml_text)
        return {"status": "completed"}

    result = await run_generation_pipeline(
        prompt="p", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is False
    assert executed == []
    assert len(result.attempts) == MAX_STATIC_ATTEMPTS
    assert all(a.stage == "static" and not a.success for a in result.attempts)


BROKEN_TEMPLATE_YAML = """
name: t
nodes:
  - id: first
    type: Literal
    config:
      value: hello
  - id: second
    type: Echo
    config:
      template: "{{first.val}}"
edges:
  - from: first
    to: second
entry: first
exit: second
"""


@pytest.mark.asyncio
async def test_deterministic_fix_resolves_a_retry_without_a_second_llm_call():
    """The static loop tries the free, deterministic fixer on a failed
    attempt's report before spending another LLM call — a single typo'd
    template field (an obviously fixable case) should never need a second
    generate_yaml call."""
    generate_calls = 0

    async def generate_yaml(prompt, prior_yaml, feedback):
        nonlocal generate_calls
        generate_calls += 1
        return BROKEN_TEMPLATE_YAML

    async def static_check(yaml_text):
        if "{{first.val}}" in yaml_text:
            issue = PreflightIssue(
                code="TEMPLATE_UNKNOWN_OUTPUT_FIELD",
                severity=PreflightSeverity.ERROR,
                message="Literal 'first' has no output field 'val'.",
                path="nodes.1.config.template",
                node_id="second",
                suggestion="Available fields: value.",
            )
            return WorkflowPreflightReport(valid=False, issues=[issue]), {}
        return WorkflowPreflightReport(valid=True), {}

    async def execute(yaml_text, inputs):
        return {"status": "completed"}

    result = await run_generation_pipeline(
        prompt="p", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is True
    assert generate_calls == 1
    static_attempts = [a for a in result.attempts if a.stage == "static"]
    assert any("Deterministic fix" in a.detail for a in static_attempts)


@pytest.mark.asyncio
async def test_real_execution_failure_is_repaired_then_succeeds():
    async def generate_yaml(prompt, prior_yaml, feedback):
        return "attempt" if prior_yaml is None else "attempt-fixed"

    async def static_check(yaml_text):
        return VALID_REPORT, {}

    async def execute(yaml_text, inputs):
        if yaml_text == "attempt":
            return {"status": "failed", "error": "node X raised a RuntimeError"}
        return {"status": "completed"}

    result = await run_generation_pipeline(
        prompt="p", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is True
    assert result.yaml == "attempt-fixed"
    real_attempts = [a for a in result.attempts if a.stage == "real_execution"]
    assert real_attempts[0].success is False
    assert "RuntimeError" in real_attempts[0].detail
    assert real_attempts[1].success is True


@pytest.mark.asyncio
async def test_exhausts_real_execution_attempts_and_returns_failure_with_best_yaml():
    async def generate_yaml(prompt, prior_yaml, feedback):
        return "never-runs"

    async def static_check(yaml_text):
        return VALID_REPORT, {}

    async def execute(yaml_text, inputs):
        return {"status": "failed", "error": "always fails"}

    result = await run_generation_pipeline(
        prompt="p", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is False
    assert result.yaml == "never-runs"
    real_attempts = [a for a in result.attempts if a.stage == "real_execution"]
    assert len(real_attempts) == MAX_REAL_EXECUTION_ATTEMPTS
    assert result.execution_result == {"status": "failed", "error": "always fails"}


@pytest.mark.asyncio
async def test_paused_run_counts_as_success_not_failure():
    async def generate_yaml(prompt, prior_yaml, feedback):
        return "yaml"

    async def static_check(yaml_text):
        return VALID_REPORT, {}

    async def execute(yaml_text, inputs):
        return {"status": "paused"}

    result = await run_generation_pipeline(
        prompt="p", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is True
    assert result.execution_result == {"status": "paused"}


@pytest.mark.asyncio
async def test_required_file_input_with_no_sample_skips_execution_gracefully():
    from app.runtime.schema import WorkflowInputSpec

    async def generate_yaml(prompt, prior_yaml, feedback):
        return "yaml"

    async def static_check(yaml_text):
        return VALID_REPORT, {"upload": WorkflowInputSpec(type="file", required=True)}

    executed = []

    async def execute(yaml_text, inputs):
        executed.append(inputs)
        return {"status": "completed"}

    result = await run_generation_pipeline(
        prompt="p", sample_inputs=None,
        generate_yaml=generate_yaml, static_check=static_check, execute=execute,
    )

    assert result.success is True  # static-clean, just couldn't be executed
    assert executed == []
    assert result.execution_skipped_reason is not None
    assert "upload" in result.execution_skipped_reason


class _RaisingLLM:
    """A stand-in for a provider SDK failure (auth, rate limit, insufficient
    credit, timeout, ...) — none of these get translated into one of
    app.llm.errors' provider-neutral types before reaching the endpoint, so
    the endpoint itself must turn an arbitrary exception into a clean HTTP
    error instead of crashing with a bare 500."""

    def with_context(self, **_kwargs):
        return self

    async def complete(self, *_args, **_kwargs):
        raise RuntimeError(
            "Error code: 400 - insufficient credit balance to access the API."
        )


@pytest.mark.asyncio
async def test_an_upstream_llm_failure_surfaces_as_a_clean_502_not_a_bare_500():
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services={"llm": _RaisingLLM()})),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_workflow_endpoint(
            GenerateWorkflowRequest(prompt="Research job postings and draft a memo."),
            request,
            USER,
        )

    assert exc_info.value.status_code == 502
    assert "insufficient credit balance" in exc_info.value.detail


class _RecordingLLM:
    """Captures every call's system/user prompt instead of hitting a real
    model — lets a test assert on exactly what the LLM was told."""

    def __init__(self, response_text: str):
        self._response_text = response_text
        self.calls: list[dict[str, str]] = []

    def with_context(self, **_kwargs):
        return self

    async def complete(self, *, model, system, user, **_kwargs):
        self.calls.append({"model": model, "system": system, "user": user})
        return SimpleNamespace(text=self._response_text)


@pytest.mark.asyncio
async def test_repair_mode_frames_the_call_as_editing_the_existing_document():
    """Regression test for a real incident: autofix's repair call used to
    reuse /generate's "Describe workflow: {base_prompt}" framing with a fixed
    repair instruction as `base_prompt`, which one LLM call read as a literal
    spec and built a workflow whose job was "repair workflow YAML" instead of
    fixing the user's actual workflow. `mode="repair"` must never construct
    that "Describe workflow: ..." phrasing, and must present the existing
    document plus the validation feedback directly."""
    original_yaml = "name: Real User Workflow\nnodes:\n  - id: a\n    type: Literal\n    config:\n      value: 1\n"
    llm = _RecordingLLM(original_yaml)
    generate_yaml = build_llm_yaml_generator(llm, services={}, scope="s", mode="repair")

    result = await generate_yaml(
        "Fix the validation errors in this workflow YAML while preserving its structure and intent.",
        original_yaml,
        "Your previous YAML failed static validation with these issues: WORKFLOW_SCHEMA: broken.",
    )

    assert result == original_yaml.strip()
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert "Describe workflow:" not in call["user"]
    assert original_yaml in call["user"]
    assert "WORKFLOW_SCHEMA: broken" in call["user"]
    assert "repair" in call["system"].lower()
    assert call["system"] == _REPAIR_SYSTEM_PROMPT_TEMPLATE.format(catalog=_node_type_catalog())


class _FixedYamlLLM:
    """Always returns the same minimal, valid workflow — enough to clear
    static preflight immediately so the pipeline reaches the real-execution
    stage, which is what this test actually exercises."""

    def __init__(self, yaml_text: str):
        self._yaml_text = yaml_text

    def with_context(self, **_kwargs):
        return self

    async def complete(self, *_args, **_kwargs):
        return SimpleNamespace(text=self._yaml_text)


_MINIMAL_VALID_YAML = "name: t\nnodes:\n  - id: a\n    type: Literal\n    config:\n      value: 1\n"


@pytest.mark.asyncio
async def test_a_real_execution_crash_is_reported_as_a_failed_run_not_lost(monkeypatch):
    """Reproduces a real incident: a structurally-valid resolved config that
    was wrong at runtime (WebSearchAgent's `query` resolved to None) raised
    straight out of run_workflow instead of coming back as a normal failed
    result, so the whole generation attempt was lost — no yaml, no detail,
    just a 502. `execute()` must catch this and report it like any other
    failed run so the caller still gets the (otherwise valid) YAML back."""

    async def _raising_run_workflow(*_args, **_kwargs):
        raise ValueError("1 validation error for WebSearchAgentConfig")

    monkeypatch.setattr("app.api.workflow_generation.run_workflow", _raising_run_workflow)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(services={"llm": _FixedYamlLLM(_MINIMAL_VALID_YAML)}),
        ),
    )

    result = await generate_workflow_endpoint(
        GenerateWorkflowRequest(prompt="A trivial workflow."), request, USER,
    )

    assert result["yaml"].strip() == _MINIMAL_VALID_YAML.strip()
    assert result["execution_result"]["status"] == "failed"
    assert "WebSearchAgentConfig" in result["execution_result"]["error"]


_YAML_WITH_A_REQUIRED_INPUT = (
    "name: t\n"
    "inputs:\n"
    "  topic:\n"
    "    type: text\n"
    "    required: true\n"
    "nodes:\n"
    "  - id: a\n"
    "    type: Echo\n"
    "    config:\n"
    "      template: 'Topic: {{inputs.topic}}'\n"
)


@pytest.mark.asyncio
async def test_a_correctly_required_input_does_not_fail_static_check_with_no_sample_given():
    """Reproduces a real incident: a workflow correctly declaring a required
    input (exactly the fix for the earlier "input silently resolves to
    None" bug) used to fail its OWN generation-time static check with
    REQUIRED_INPUT_MISSING, because static_check coerced the near-always-
    absent `sample_inputs` into `{}` — which preflight reads as "these are
    the real inputs, and none were given" rather than "no real inputs exist
    yet, this is a structural check." A /generate caller essentially never
    passes sample_inputs, so this broke required inputs entirely."""
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services={"llm": _FixedYamlLLM(_YAML_WITH_A_REQUIRED_INPUT)},
            ),
        ),
    )

    result = await generate_workflow_endpoint(
        GenerateWorkflowRequest(prompt="A workflow with a required input."), request, USER,
    )

    codes = [issue["code"] for issue in (result["preflight_report"] or {}).get("issues", [])]
    assert "REQUIRED_INPUT_MISSING" not in codes
    assert result["preflight_report"]["valid"] is True
