from __future__ import annotations

import pytest

from app.api.workflow_generation import (
    _EXAMPLE_WORKFLOW_YAML,
    _node_type_catalog,
    MAX_REAL_EXECUTION_ATTEMPTS,
    MAX_STATIC_ATTEMPTS,
    run_generation_pipeline,
)
from app.runtime.preflight import (
    PreflightIssue,
    PreflightSeverity,
    WorkflowPreflightReport,
    preflight_workflow_yaml,
)

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


def test_example_workflow_yaml_passes_structural_preflight():
    """The worked example in the generation system prompt is the model's
    only concrete template of a multi-node reference chain — if it doesn't
    pass preflight itself, it's actively teaching the bug it's meant to
    prevent."""
    report = preflight_workflow_yaml(_EXAMPLE_WORKFLOW_YAML)
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
