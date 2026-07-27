from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.workflows import RunRequest, run
from app.nodes.registry import NodeRegistry
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import (
    DuplicateYamlKeyError,
    preflight_workflow_for_run,
    preflight_workflow_yaml,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role


VALID = """
name: Preflight Test
inputs:
  message:
    type: text
    required: true
nodes:
  - id: first
    type: Literal
    config:
      value: hello
  - id: second
    type: Echo
    config:
      template: "{{first.value}} {{inputs.message}}"
edges:
  - from: first
    to: second
entry: first
exit: second
"""


def codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_claim_evidence_verifier_is_auto_discovered():
    assert NodeRegistry.get("ClaimEvidenceVerifier").__name__ == (
        "ClaimEvidenceVerifier"
    )


def test_valid_preflight_compiles_and_spends_no_tokens():
    class ExplodingLLM:
        def __getattr__(self, name):
            raise AssertionError(f"Preflight tried to call LLM method {name}")

    report = preflight_workflow_yaml(
        VALID,
        provided_inputs={"message": "world"},
        services={"llm": ExplodingLLM()},
    )

    assert report.valid is True
    assert report.tokens_spent == 0
    assert not report.errors
    assert next(
        check for check in report.checks if check.name == "graph_compile"
    ).status == "passed"


def test_duplicate_yaml_keys_are_blocked_in_preflight_and_loader():
    duplicate = """
name: Duplicate
nodes:
  - id: same
    id: overwritten
    type: Literal
    config:
      value: ok
"""
    report = preflight_workflow_yaml(duplicate)

    assert report.valid is False
    assert "YAML_DUPLICATE_KEY" in codes(report)
    with pytest.raises(DuplicateYamlKeyError):
        load_workflow_from_string(duplicate)


def test_unknown_node_type_has_actionable_error():
    report = preflight_workflow_yaml(
        VALID.replace("type: Echo", "type: ClaimEvidenceVerifer")
    )

    issue = next(
        item
        for item in report.errors
        if item.code == "UNKNOWN_NODE_TYPE"
    )
    assert report.valid is False
    assert "ClaimEvidenceVerifier" in (issue.suggestion or "")


def test_bad_node_config_and_unknown_config_field_are_blocked():
    invalid = VALID.replace(
        'template: "{{first.value}} {{inputs.message}}"',
        "templat: wrong",
    )
    report = preflight_workflow_yaml(invalid)

    assert "UNKNOWN_NODE_CONFIG_FIELD" in codes(report)
    assert "NODE_CONFIG_INVALID" in codes(report)


def test_bad_template_paths_are_blocked_before_execution():
    invalid = VALID.replace(
        "{{first.value}} {{inputs.message}}",
        "{{missing.value}} {{inputs.typo}}",
    )
    report = preflight_workflow_yaml(invalid)

    assert "TEMPLATE_UNKNOWN_NODE" in codes(report)
    assert "TEMPLATE_UNKNOWN_INPUT" in codes(report)


def test_downstream_template_reference_is_blocked():
    invalid = VALID.replace(
        'value: hello',
        'value: "{{second.text}}"',
    )
    report = preflight_workflow_yaml(invalid)

    assert "TEMPLATE_NOT_UPSTREAM" in codes(report)


def test_transform_structured_field_must_be_declared():
    workflow = """
name: Parsed field typo
inputs:
  text:
    type: text
nodes:
  - id: extract
    type: TransformAgent
    config:
      model: gpt-5-mini
      prompt_template: "{{inputs.text}}"
      output_schema:
        title: str
  - id: use
    type: Echo
    config:
      template: "{{extract.parsed.titel}}"
edges:
  - from: extract
    to: use
"""
    report = preflight_workflow_yaml(workflow)

    assert "TEMPLATE_UNKNOWN_STRUCTURED_FIELD" in codes(report)


def test_router_rule_and_branch_names_must_match():
    workflow = """
name: Bad router
nodes:
  - id: route
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: "yes"
          default: true
  - id: done
    type: Literal
    config:
      value: ok
edges:
  - from: route
    condition: route
    branches:
      "no": done
"""
    report = preflight_workflow_yaml(workflow)

    assert "ROUTER_BRANCH_MISMATCH" in codes(report)


def test_unreachable_nodes_are_blocked():
    workflow = """
name: Unreachable
nodes:
  - id: first
    type: Literal
    config:
      value: ok
  - id: orphan
    type: Literal
    config:
      value: unused
entry: first
exit: first
"""
    report = preflight_workflow_yaml(workflow)

    assert "UNREACHABLE_NODE" in codes(report)


def test_unapproved_model_is_blocked():
    workflow = """
name: Bad model
nodes:
  - id: writer
    type: TransformAgent
    config:
      model: made-up-model
      prompt_template: hello
"""
    report = preflight_workflow_yaml(workflow)

    assert "MODEL_NOT_IN_CATALOG" in codes(report)


@pytest.mark.asyncio
async def test_strict_run_preflight_reports_all_missing_services():
    workflow = """
name: Service test
nodes:
  - id: writer
    type: TransformAgent
    config:
      model: gpt-5-mini
      prompt_template: hello
"""
    report = await preflight_workflow_for_run(
        workflow,
        provided_inputs={},
        services={},
    )

    assert report.valid is False
    assert {
        issue.path
        for issue in report.errors
        if issue.code == "REQUIRED_SERVICE_MISSING"
    } == {
        "services.audit_db",
        "services.cost_ledger",
        "services.event_bus",
        "services.llm",
    }
    assert report.tokens_spent == 0


@pytest.mark.asyncio
async def test_run_api_blocks_unknown_node_before_history_or_llm():
    class GuardLLM:
        calls = 0

        def __getattr__(self, name):
            self.calls += 1
            raise AssertionError(f"LLM must not be touched during preflight: {name}")

    class GuardDB:
        writes = 0

        def __getitem__(self, name):
            self.writes += 1
            raise AssertionError("Run history must not be created for invalid YAML")

    llm = GuardLLM()
    db = GuardDB()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services={"llm": llm, "audit_db": db}
            )
        )
    )
    user = CurrentUser(
        username="user@example.com",
        role=Role.CONSULTANT,
        session_id=None,
    )
    invalid = VALID.replace("type: Echo", "type: ClaimEvidenceVerifer")

    with pytest.raises(HTTPException) as caught:
        await run(
            RunRequest(
                workflow_yaml=invalid,
                inputs={"message": "world"},
            ),
            request,
            user,
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["preflight"]["tokens_spent"] == 0
    assert llm.calls == 0
    assert db.writes == 0


def test_every_shipped_workflow_passes_structural_preflight():
    failures: list[str] = []
    for path in sorted(Path("workflows").glob("*.yaml")):
        report = preflight_workflow_yaml(path.read_text())
        if not report.valid:
            failures.append(
                f"{path.name}: "
                + "; ".join(issue.message for issue in report.errors)
            )

    assert failures == []
