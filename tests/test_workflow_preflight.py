from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.workflows import (
    RunRequest,
    ValidateWorkflowRequest,
    run,
    validate_workflow,
)
from app.llm.registry import ModelAccessResult
from app.nodes.registry import NodeRegistry
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import (
    DuplicateYamlKeyError,
    PreflightSeverity,
    preflight_workflow_for_run,
    preflight_workflow_yaml,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.file_inputs import workflow_input_prefix


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


class HealthyAuditDB:
    async def command(self, _name):
        return {"ok": 1}


class AccessProbeLLM:
    def __init__(self, denied: set[str]):
        self.denied = denied
        self.generation_calls = 0

    async def probe_model_access(self, models):
        return {
            model: ModelAccessResult(
                available=model not in self.denied,
                reason=(
                    "provider confirmed model access"
                    if model not in self.denied
                    else "provider rejected this model (HTTP 403)"
                ),
                status_code=403 if model in self.denied else None,
            )
            for model in models
        }

    def __getattr__(self, _name):
        self.generation_calls += 1
        raise AssertionError("Strict preflight must not generate tokens")


def test_claim_evidence_verifier_is_auto_discovered():
    assert NodeRegistry.get("ClaimEvidenceVerifier").__name__ == (
        "ClaimEvidenceVerifier"
    )


def test_proposal_evidence_and_html_renderer_nodes_are_auto_discovered():
    assert NodeRegistry.get(
        "ScholarlyCandidateDiscoveryAgent"
    ).__name__ == "ScholarlyCandidateDiscoveryAgent"
    assert NodeRegistry.get(
        "ResearchSourceAcquirer"
    ).__name__ == "ResearchSourceAcquirer"
    assert NodeRegistry.get(
        "ProposalEvidenceFactoryAgent"
    ).__name__ == "ProposalEvidenceFactoryAgent"
    assert NodeRegistry.get(
        "HorizonHTMLProposalRenderer"
    ).__name__ == "HorizonHTMLProposalRenderer"


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


def test_unknown_input_and_variable_suggest_closest_name():
    workflow = """
name: Typo suggestions
inputs:
  message:
    type: text
static_variables:
  - name: greeting
    type: text
    value: hi
nodes:
  - id: use
    type: Echo
    config:
      template: "{{inputs.mesage}} {{variables.greting}}"
"""
    report = preflight_workflow_yaml(workflow)

    input_issue = next(i for i in report.errors if i.code == "TEMPLATE_UNKNOWN_INPUT")
    variable_issue = next(i for i in report.errors if i.code == "TEMPLATE_UNKNOWN_VARIABLE")
    assert input_issue.suggestion == "Did you mean message?"
    assert variable_issue.suggestion == "Did you mean greeting?"


def test_unknown_node_suggestion_excludes_self_and_prefers_upstream():
    # "load_dat" fuzzy-matches both "load_data" (the real upstream typo
    # target) and "use_data" (the node making the reference, and therefore
    # never a valid answer) closely enough for a naive difflib match to
    # offer both — the suggestion must narrow to the one that could ever
    # actually resolve.
    workflow = """
name: Ambiguous fuzzy match
nodes:
  - id: load_data
    type: Literal
    config:
      value: hello
  - id: use_data
    type: Echo
    config:
      template: "{{outputs.load_dat.value}}"
edges:
  - from: load_data
    to: use_data
entry: load_data
exit: use_data
"""
    report = preflight_workflow_yaml(workflow)

    issue = next(i for i in report.errors if i.code == "TEMPLATE_UNKNOWN_NODE")
    assert issue.suggestion == "Did you mean load_data?"


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
async def test_strict_preflight_excludes_denied_auto_candidate_without_tokens():
    workflow = """
name: Model access test
nodes:
  - id: writer
    type: TransformAgent
    selected_model: auto
    allowed_models: [gpt-5.6-sol, gpt-5]
    config:
      model: gpt-5.6-sol
      prompt_template: Draft the methodology.
"""
    llm = AccessProbeLLM({"gpt-5.6-sol"})
    report = await preflight_workflow_for_run(
        workflow,
        provided_inputs={},
        services={
            "llm": llm,
            "cost_ledger": object(),
            "audit_db": HealthyAuditDB(),
            "event_bus": object(),
        },
    )

    assert report.valid is True
    assert "AUTO_MODEL_CANDIDATE_EXCLUDED" in codes(report)
    assert "AUTO_MODEL_ACCESS_UNAVAILABLE" not in codes(report)
    assert report.tokens_spent == 0
    assert llm.generation_calls == 0


@pytest.mark.asyncio
async def test_strict_preflight_blocks_when_auto_has_no_accessible_model():
    workflow = """
name: No model access
nodes:
  - id: writer
    type: TransformAgent
    selected_model: auto
    allowed_models: [gpt-5.6-sol]
    config:
      model: gpt-5.6-sol
      prompt_template: Draft the methodology.
"""
    report = await preflight_workflow_for_run(
        workflow,
        provided_inputs={},
        services={
            "llm": AccessProbeLLM({"gpt-5.6-sol"}),
            "cost_ledger": object(),
            "audit_db": HealthyAuditDB(),
            "event_bus": object(),
        },
    )

    assert report.valid is False
    assert "AUTO_MODEL_ACCESS_UNAVAILABLE" in codes(report)
    assert report.tokens_spent == 0


@pytest.mark.asyncio
async def test_strict_preflight_blocks_inaccessible_manual_model():
    workflow = """
name: Manual model access
nodes:
  - id: writer
    type: TransformAgent
    selected_model: gpt-5.6-sol
    allowed_models: [gpt-5.6-sol, gpt-5]
    config:
      model: gpt-5.6-sol
      prompt_template: Draft the methodology.
"""
    report = await preflight_workflow_for_run(
        workflow,
        provided_inputs={},
        services={
            "llm": AccessProbeLLM({"gpt-5.6-sol"}),
            "cost_ledger": object(),
            "audit_db": HealthyAuditDB(),
            "event_bus": object(),
        },
    )

    assert report.valid is False
    assert "MODEL_ACCESS_UNAVAILABLE" in codes(report)
    assert report.tokens_spent == 0


@pytest.mark.asyncio
async def test_minio_access_key_error_has_actionable_diagnosis():
    class InvalidCredentials(Exception):
        response = {
            "Error": {
                "Code": "InvalidAccessKeyId",
            }
        }

    class ObjectStoreClient:
        def list_buckets(self):
            raise InvalidCredentials()

    workflow = """
name: Object storage access
nodes:
  - id: load
    type: WorkflowFileLoader
    config:
      files: []
"""
    report = await preflight_workflow_for_run(
        workflow,
        provided_inputs={},
        services={
            "object_store": SimpleNamespace(client=ObjectStoreClient()),
            "audit_db": HealthyAuditDB(),
            "event_bus": object(),
        },
    )

    issue = next(
        item
        for item in report.errors
        if item.code == "OBJECT_STORE_CREDENTIALS_INVALID"
    )
    assert "MINIO_ACCESS_KEY" in (issue.suggestion or "")
    assert report.tokens_spent == 0


@pytest.mark.asyncio
async def test_full_zero_token_api_test_validates_uploaded_file_reference():
    class ObjectStoreClient:
        def list_buckets(self):
            return []

    class ObjectStore:
        client = ObjectStoreClient()

        def object_exists(self, _key):
            return True

    workflow = """
name: Uploaded input rehearsal
inputs:
  concept_note:
    type: file
    required: true
    accept: [pdf]
nodes:
  - id: load
    type: WorkflowFileLoader
    config:
      files: "{{inputs.concept_note}}"
"""
    session_id = "session-1"
    file_ref = {
        "kind": "workflow_file",
        "file_id": "wf_123",
        "name": "concept.pdf",
        "extension": ".pdf",
        "category": "pdf",
        "content_type": "application/pdf",
        "size_bytes": 100,
        "sha256": "a" * 64,
        "minio_key": (
            workflow_input_prefix(session_id) + ("a" * 64) + ".pdf"
        ),
        "parseable_text": True,
    }
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services={
                    "object_store": ObjectStore(),
                    "audit_db": HealthyAuditDB(),
                    "event_bus": object(),
                }
            )
        )
    )
    user = CurrentUser(
        username="user@example.com",
        role=Role.CONSULTANT,
        session_id=session_id,
    )

    report = await validate_workflow(
        ValidateWorkflowRequest(
            workflow_yaml=workflow,
            inputs={"concept_note": file_ref},
            check_services=True,
        ),
        request,
        user,
    )

    assert report["valid"] is True
    assert report["tokens_spent"] == 0
    assert any(
        check["name"] == "input_files"
        and check["status"] == "passed"
        for check in report["checks"]
    )


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


GUIDED_COMPLETE = """
name: Guided Preflight Test
nodes:
  - id: first
    type: Literal
    config:
      value: hello
    experience:
      display_name: Map the call requirements
      purpose: Identify what the final result must address.
      contribution: Guides evidence collection and compliance review.
      expected_output: A checked requirement matrix
      failure_message: This step could not finish; completed work remains safe.
      visibility: standard
edges: []
"""


def test_incomplete_guided_experience_blocks_preflight():
    incomplete = GUIDED_COMPLETE.replace(
        "      purpose: Identify what the final result must address.\n", ""
    )
    report = preflight_workflow_yaml(incomplete, compile_graph=False)

    assert not report.valid
    assert "GUIDED_EXPERIENCE_INCOMPLETE" in codes(report)


def test_advanced_visibility_skips_guided_experience_checks():
    incomplete_but_advanced = (
        GUIDED_COMPLETE
        .replace("      purpose: Identify what the final result must address.\n", "")
        .replace("visibility: standard", "visibility: advanced")
    )
    report = preflight_workflow_yaml(incomplete_but_advanced, compile_graph=False)

    assert report.valid
    assert "GUIDED_EXPERIENCE_INCOMPLETE" not in codes(report)


def test_technical_guided_display_name_warns_but_does_not_block():
    technical_name = GUIDED_COMPLETE.replace(
        "display_name: Map the call requirements",
        "display_name: Run TransformAgent node",
    )
    report = preflight_workflow_yaml(technical_name, compile_graph=False)

    assert report.valid
    assert "GUIDED_COPY_TECHNICAL" in codes(report)
    warning = next(
        issue for issue in report.issues if issue.code == "GUIDED_COPY_TECHNICAL"
    )
    assert warning.severity == PreflightSeverity.WARNING


def test_show_agent_role_without_role_blocks_preflight():
    missing_role = GUIDED_COMPLETE.replace(
        "visibility: standard", "visibility: standard\n      show_agent_role: true"
    )
    report = preflight_workflow_yaml(missing_role, compile_graph=False)

    assert not report.valid
    assert "GUIDED_ROLE_MISSING" in codes(report)


def test_legacy_workflow_with_no_experience_has_no_guided_issues():
    report = preflight_workflow_yaml(VALID, compile_graph=False)

    assert not any(issue.code.startswith("GUIDED_") for issue in report.issues)
    assert any(check.name == "guided_experience" for check in report.checks)
