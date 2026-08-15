from __future__ import annotations

import pytest

from app.runtime.autofix import (
    MAX_LLM_REPAIR_ATTEMPTS,
    NOT_AUTOFIXABLE_CODES,
    _preserves_identity,
    apply_deterministic_fixes,
    repair_with_llm,
)
from app.runtime.preflight import (
    PreflightIssue,
    PreflightSeverity,
    WorkflowPreflightReport,
    preflight_workflow_yaml,
)


def _report(*issues: PreflightIssue) -> WorkflowPreflightReport:
    return WorkflowPreflightReport(valid=not issues, issues=list(issues))


def _error(code: str, message: str, **kwargs) -> PreflightIssue:
    return PreflightIssue(code=code, severity=PreflightSeverity.ERROR, message=message, **kwargs)


def _warning(code: str, message: str, **kwargs) -> PreflightIssue:
    return PreflightIssue(code=code, severity=PreflightSeverity.WARNING, message=message, **kwargs)


def codes(report: WorkflowPreflightReport) -> set[str]:
    return {issue.code for issue in report.issues}


# ---------------------------------------------------------------------------
# TEMPLATE_UNKNOWN_OUTPUT_FIELD
# ---------------------------------------------------------------------------

def test_output_field_single_available_candidate_is_fixed():
    yaml_text = """
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
    issue = _error(
        "TEMPLATE_UNKNOWN_OUTPUT_FIELD",
        "Literal 'first' has no output field 'val'.",
        path="nodes.1.config.template",
        node_id="second",
        suggestion="Available fields: value.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "{{first.value}}" in result.yaml_text
    assert result.fixes_applied
    report = preflight_workflow_yaml(result.yaml_text)
    assert "TEMPLATE_UNKNOWN_OUTPUT_FIELD" not in codes(report)


def test_output_field_close_fuzzy_match_is_fixed():
    yaml_text = """
name: t
nodes:
  - id: extract
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "summarize this"
  - id: second
    type: Echo
    config:
      template: "{{extract.rw}}"
edges:
  - from: extract
    to: second
entry: extract
exit: second
"""
    issue = _error(
        "TEMPLATE_UNKNOWN_OUTPUT_FIELD",
        "TransformAgent 'extract' has no output field 'rw'.",
        path="nodes.1.config.template",
        node_id="second",
        suggestion="Available fields: parsed, raw.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "{{extract.raw}}" in result.yaml_text


def test_output_field_ambiguous_candidates_are_left_unchanged():
    """This is the exact shape of the originally reported bug: a
    TransformAgent with no declared output_schema exposes two equally
    plausible fields (raw, parsed), and the bad reference ("output") isn't a
    close match to either — so the deterministic fixer must not guess, and
    the issue is left for the LLM repair stage."""
    yaml_text = """
name: t
nodes:
  - id: extract
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "summarize this"
  - id: second
    type: Echo
    config:
      template: "{{extract.output}}"
edges:
  - from: extract
    to: second
entry: extract
exit: second
"""
    issue = _error(
        "TEMPLATE_UNKNOWN_OUTPUT_FIELD",
        "TransformAgent 'extract' has no output field 'output'.",
        path="nodes.1.config.template",
        node_id="second",
        suggestion="Available fields: parsed, raw.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed is False
    assert result.yaml_text == yaml_text


# ---------------------------------------------------------------------------
# TEMPLATE_UNKNOWN_STRUCTURED_FIELD
# ---------------------------------------------------------------------------

def test_structured_field_typo_is_fixed():
    yaml_text = """
name: t
nodes:
  - id: extract
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "extract a title"
      output_schema:
        title: str
  - id: use
    type: Echo
    config:
      template: "{{extract.parsed.titel}}"
edges:
  - from: extract
    to: use
entry: extract
exit: use
"""
    issue = _error(
        "TEMPLATE_UNKNOWN_STRUCTURED_FIELD",
        "TransformAgent 'extract' does not declare structured field 'parsed.titel'.",
        path="nodes.1.config.template",
        node_id="use",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "{{extract.parsed.title}}" in result.yaml_text
    report = preflight_workflow_yaml(result.yaml_text)
    assert "TEMPLATE_UNKNOWN_STRUCTURED_FIELD" not in codes(report)


# ---------------------------------------------------------------------------
# TEMPLATE_UNKNOWN_NODE
# ---------------------------------------------------------------------------

def test_unknown_node_single_suggestion_is_fixed():
    yaml_text = """
name: t
nodes:
  - id: structure_call_text
    type: Literal
    config:
      value: hello
  - id: second
    type: Echo
    config:
      template: "{{strcture_call_text.value}}"
edges:
  - from: structure_call_text
    to: second
entry: structure_call_text
exit: second
"""
    issue = _error(
        "TEMPLATE_UNKNOWN_NODE",
        "Template references unknown node/path 'strcture_call_text.value'.",
        path="nodes.1.config.template",
        node_id="second",
        suggestion="Did you mean structure_call_text?",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "{{structure_call_text.value}}" in result.yaml_text


# ---------------------------------------------------------------------------
# TEMPLATE_UNKNOWN_INPUT / TEMPLATE_UNKNOWN_VARIABLE
# ---------------------------------------------------------------------------

def test_unknown_input_single_suggestion_is_fixed():
    yaml_text = """
name: t
inputs:
  message:
    type: text
nodes:
  - id: a
    type: Echo
    config:
      template: "{{inputs.mesage}}"
"""
    issue = _error(
        "TEMPLATE_UNKNOWN_INPUT",
        "Template references unknown input 'inputs.mesage'.",
        path="nodes.0.config.template",
        node_id="a",
        suggestion="Did you mean message?",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "{{inputs.message}}" in result.yaml_text


def test_unknown_variable_single_suggestion_is_fixed():
    yaml_text = """
name: t
static_variables:
  - name: greeting
    type: text
    value: hi
nodes:
  - id: a
    type: Echo
    config:
      template: "{{variables.greting}}"
"""
    issue = _error(
        "TEMPLATE_UNKNOWN_VARIABLE",
        "Template references unknown variable 'variables.greting'.",
        path="nodes.0.config.template",
        node_id="a",
        suggestion="Did you mean greeting?",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "{{variables.greeting}}" in result.yaml_text


def test_unknown_input_without_suggestion_is_left_unchanged():
    yaml_text = """
name: t
inputs:
  message:
    type: text
nodes:
  - id: a
    type: Echo
    config:
      template: "{{inputs.totally_unrelated}}"
"""
    issue = _error(
        "TEMPLATE_UNKNOWN_INPUT",
        "Template references unknown input 'inputs.totally_unrelated'.",
        path="nodes.0.config.template",
        node_id="a",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed is False


# ---------------------------------------------------------------------------
# UNKNOWN_NODE_TYPE
# ---------------------------------------------------------------------------

def test_unknown_node_type_single_suggestion_is_fixed():
    yaml_text = """
name: t
nodes:
  - id: a
    type: TransfomAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "hi"
"""
    issue = _error(
        "UNKNOWN_NODE_TYPE",
        "Unknown node type: TransfomAgent",
        path="nodes.0.type",
        node_id="a",
        suggestion="Use one of: TransformAgent.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "type: TransformAgent" in result.yaml_text


# ---------------------------------------------------------------------------
# MODEL_NOT_IN_CATALOG
# ---------------------------------------------------------------------------

def test_model_not_in_catalog_close_match_is_fixed():
    yaml_text = """
name: t
nodes:
  - id: a
    type: TransformAgent
    config:
      model: gpt-5-min
      prompt_template: "hi"
"""
    issue = _error(
        "MODEL_NOT_IN_CATALOG",
        "Model 'gpt-5-min' is not in the approved model catalog.",
        path="nodes.0.config.model",
        node_id="a",
        suggestion="Choose one of: claude-opus-5, claude-sonnet-4-5.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "model: gpt-5-mini" in result.yaml_text
    assert "confirm" not in result.fixes_applied[0]


def test_model_not_in_catalog_falls_back_to_default_when_no_close_match():
    yaml_text = """
name: t
nodes:
  - id: a
    type: TransformAgent
    config:
      model: totally-unknown-model-xyz
      prompt_template: "hi"
"""
    issue = _error(
        "MODEL_NOT_IN_CATALOG",
        "Model 'totally-unknown-model-xyz' is not in the approved model catalog.",
        path="nodes.0.config.model",
        node_id="a",
        suggestion="Choose one of: claude-opus-5, claude-sonnet-4-5.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "model: claude-opus-5" in result.yaml_text
    assert "confirm" in result.fixes_applied[0]


# ---------------------------------------------------------------------------
# UNKNOWN_NODE_CONFIG_FIELD
# ---------------------------------------------------------------------------

def test_unknown_node_config_field_is_renamed_on_close_match():
    yaml_text = """
name: t
nodes:
  - id: a
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      promt_template: "hi"
"""
    issue = _error(
        "UNKNOWN_NODE_CONFIG_FIELD",
        "TransformAgent does not define config field 'promt_template'.",
        path="nodes.0.config.promt_template",
        node_id="a",
        suggestion="Remove the field or correct its spelling.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "prompt_template: hi" in result.yaml_text
    assert "promt_template" not in result.yaml_text


def test_unknown_node_config_field_is_dropped_when_no_close_match():
    yaml_text = """
name: t
nodes:
  - id: a
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "hi"
      totally_bogus_field: nonsense
"""
    issue = _error(
        "UNKNOWN_NODE_CONFIG_FIELD",
        "TransformAgent does not define config field 'totally_bogus_field'.",
        path="nodes.0.config.totally_bogus_field",
        node_id="a",
        suggestion="Remove the field or correct its spelling.",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    assert "totally_bogus_field" not in result.yaml_text


# ---------------------------------------------------------------------------
# DUPLICATE_EDGE
# ---------------------------------------------------------------------------

def test_duplicate_edge_is_removed():
    yaml_text = """
name: t
nodes:
  - id: a
    type: Literal
    config:
      value: 1
  - id: b
    type: Echo
    config:
      template: "{{a.value}}"
edges:
  - from: a
    to: b
  - from: a
    to: b
entry: a
exit: b
"""
    issue = _error(
        "DUPLICATE_EDGE",
        "Duplicate edge 'a' -> 'b'.",
        path="edges.1",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    report = preflight_workflow_yaml(result.yaml_text)
    assert len(_load_edges(result.yaml_text)) == 1
    assert "DUPLICATE_EDGE" not in codes(report)


def _load_edges(yaml_text: str) -> list:
    import yaml as yaml_module
    return yaml_module.safe_load(yaml_text)["edges"]


def _load_node(yaml_text: str, node_id: str) -> dict:
    import yaml as yaml_module
    raw = yaml_module.safe_load(yaml_text)
    return next(node for node in raw["nodes"] if node["id"] == node_id)


# ---------------------------------------------------------------------------
# ROUTER_MULTIPLE_DEFAULTS / ROUTER_DUPLICATE_RULE
# ---------------------------------------------------------------------------

_ROUTER_WORKFLOW = """
name: t
nodes:
  - id: classify
    type: Literal
    config:
      value: a
  - id: router
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: a
          default: true
          then: []
        - name: b
          default: true
          then: []
edges:
  - from: classify
    to: router
    condition: route
    branches:
      a: classify
      b: classify
entry: classify
"""


def test_router_multiple_defaults_keeps_first():
    issue = _error(
        "ROUTER_MULTIPLE_DEFAULTS",
        "Router may define at most one default rule.",
        path="nodes.1.config.rules",
        node_id="router",
    )
    result = apply_deterministic_fixes(_ROUTER_WORKFLOW, _report(issue))

    assert result.changed
    rules = _load_node(result.yaml_text, "router")["config"]["rules"]
    assert [rule["default"] for rule in rules] == [True, False]


def test_router_duplicate_rule_keeps_first_occurrence():
    yaml_text = _ROUTER_WORKFLOW.replace(
        "        - name: b\n          default: true\n          then: []",
        "        - name: a\n          default: false\n          then: []",
    )
    issue = _error(
        "ROUTER_DUPLICATE_RULE",
        "Router has duplicate rule names: ['a'].",
        path="nodes.1.config.rules",
        node_id="router",
    )
    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed
    rules = _load_node(result.yaml_text, "router")["config"]["rules"]
    assert len(rules) == 1
    assert rules[0]["default"] is True


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------

def test_not_autofixable_codes_are_never_touched():
    yaml_text = "name: t\nnodes:\n  - id: a\n    type: RAGAgent\n    config: {}\n"
    issue = _error("REQUIRED_SERVICE_MISSING", "retriever is not configured.")
    assert issue.code in NOT_AUTOFIXABLE_CODES

    result = apply_deterministic_fixes(yaml_text, _report(issue))

    assert result.changed is False
    assert result.yaml_text == yaml_text


def test_warnings_are_never_touched_even_with_a_handled_code():
    yaml_text = """
name: t
nodes:
  - id: a
    type: TransfomAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "hi"
"""
    warning = _warning(
        "UNKNOWN_NODE_TYPE",
        "Unknown node type: TransfomAgent",
        path="nodes.0.type",
        suggestion="Use one of: TransformAgent.",
    )
    report = WorkflowPreflightReport(valid=True, issues=[warning])
    result = apply_deterministic_fixes(yaml_text, report)

    assert result.changed is False
    assert result.yaml_text == yaml_text


# ---------------------------------------------------------------------------
# repair_with_llm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repair_with_llm_succeeds_within_attempts():
    attempt_count = 0

    async def generate_yaml(base_prompt, prior_yaml, feedback):
        nonlocal attempt_count
        attempt_count += 1
        return f"attempt-{attempt_count}"

    async def static_check(yaml_text):
        if yaml_text == "attempt-2":
            return WorkflowPreflightReport(valid=True)
        return _report(_error("WORKFLOW_SCHEMA", "still broken"))

    initial_report = _report(_error("WORKFLOW_SCHEMA", "broken"))
    final_yaml, final_report, attempts = await repair_with_llm(
        "original", initial_report,
        static_check=static_check, generate_yaml=generate_yaml,
    )

    assert final_yaml == "attempt-2"
    assert final_report.valid
    assert len(attempts) == 2
    assert attempts[-1].success is True


@pytest.mark.asyncio
async def test_repair_with_llm_gives_up_after_max_attempts():
    async def generate_yaml(base_prompt, prior_yaml, feedback):
        return "still-broken"

    async def static_check(yaml_text):
        return _report(_error("WORKFLOW_SCHEMA", "still broken"))

    initial_report = _report(_error("WORKFLOW_SCHEMA", "broken"))
    final_yaml, final_report, attempts = await repair_with_llm(
        "original", initial_report,
        static_check=static_check, generate_yaml=generate_yaml,
    )

    assert final_report.valid is False
    assert len(attempts) == MAX_LLM_REPAIR_ATTEMPTS


# ---------------------------------------------------------------------------
# _preserves_identity / repair_with_llm identity safety net
#
# Regression coverage for a real incident: autofix's LLM repair call
# returned an entirely different, generic "Workflow YAML Validation Repair"
# workflow (well-formed, passes preflight) instead of the user's actual
# workflow with its validation errors fixed. Nothing in the old loop caught
# this because it only checked static validity, which the wrong workflow
# satisfied trivially.
# ---------------------------------------------------------------------------

_REAL_USER_WORKFLOW = """
name: Pump CRM Orchestrator
nodes:
  - id: interpret_customer_inquiry
    type: AITaskAgent
    config:
      task: analyze
      model: auto
      input: "{{inputs.message}}"
  - id: route
    type: Echo
    config:
      template: "{{interpret_customer_inquiry.result}}"
"""

_UNRELATED_META_WORKFLOW = """
name: Workflow YAML Validation Repair
nodes:
  - id: repair_yaml
    type: AITaskAgent
    config:
      task: analyze
      model: auto
      input: "{{inputs.workflow_yaml}}"
  - id: corrected_workflow
    type: Echo
    config:
      template: "{{repair_yaml.result}}"
"""


def test_preserves_identity_accepts_a_genuine_repair():
    # Same name, same nodes, one field's value corrected in place.
    repaired = _REAL_USER_WORKFLOW.replace(
        '"{{interpret_customer_inquiry.result}}"',
        '"{{interpret_customer_inquiry.result.summary}}"',
    )
    assert _preserves_identity(_REAL_USER_WORKFLOW, repaired) is True


def test_preserves_identity_rejects_an_unrelated_workflow():
    assert _preserves_identity(_REAL_USER_WORKFLOW, _UNRELATED_META_WORKFLOW) is False


def test_preserves_identity_is_vacuous_for_unparseable_text():
    # Matches repair_with_llm's own unit tests, which exercise the control
    # flow with plain non-YAML placeholder strings rather than real
    # documents — the identity check must not reject those.
    assert _preserves_identity("original", "attempt-1") is True


@pytest.mark.asyncio
async def test_repair_with_llm_rejects_an_unrelated_workflow_and_keeps_retrying():
    async def generate_yaml(base_prompt, prior_yaml, feedback):
        return _UNRELATED_META_WORKFLOW

    async def static_check(yaml_text):
        # The unrelated meta-workflow is itself perfectly valid — this is
        # exactly what let it slip through before the identity check existed.
        return WorkflowPreflightReport(valid=True)

    initial_report = _report(_error("WORKFLOW_SCHEMA", "broken"))
    final_yaml, final_report, attempts = await repair_with_llm(
        _REAL_USER_WORKFLOW, initial_report,
        static_check=static_check, generate_yaml=generate_yaml,
    )

    assert final_yaml == _REAL_USER_WORKFLOW
    assert final_report.valid is False
    assert len(attempts) == MAX_LLM_REPAIR_ATTEMPTS
    assert all(not attempt.success for attempt in attempts)
    assert all("Rejected" in attempt.detail for attempt in attempts)
