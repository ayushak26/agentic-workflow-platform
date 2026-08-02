from __future__ import annotations

import pytest
from pydantic import ValidationError

import app.nodes  # noqa: F401
from app.runtime.domain_state import DomainStateRegistry, merge_domain_state
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.schema import NodeSpec, WorkflowSpec


def test_complete_workflow_contract_survives_validation():
    spec = load_workflow_from_string(
        """
name: Contract Test
version: "2.0"
use_case: generic
inputs:
  message:
    type: text
static_variables:
  - name: policy
    type: text
    value: grounded-only
nodes:
  - id: echo
    type: Echo
    selected_model:
    config:
      template: "{{variables.policy}}: {{inputs.message}}"
edges: []
output:
  include_input: true
  nodes:
    - node_id: echo
      flatten: true
"""
    )

    assert spec.use_case == "generic"
    assert spec.static_variables[0].value == "grounded-only"
    assert spec.output is not None
    assert spec.output.include_input is True
    assert spec.output.nodes[0].node_id == "echo"


def test_node_defaults_are_not_shared():
    first = NodeSpec(id="a", type="Echo")
    second = NodeSpec(id="b", type="Echo")
    first.config["template"] = "changed"
    first.allowed_models.append("local-model")

    assert second.config == {}
    assert "local-model" not in second.allowed_models


def test_selected_model_must_be_allowed():
    with pytest.raises(ValidationError):
        NodeSpec(
            id="writer",
            type="TransformAgent",
            selected_model="not-approved",
        )


def test_selected_model_overrides_saved_node_config():
    node = NodeSpec(
        id="writer",
        type="TransformAgent",
        config={"model": "claude-haiku-4-5"},
        selected_model="gpt-5-mini",
    )

    assert node.effective_config()["model"] == "gpt-5-mini"
    assert node.config["model"] == "claude-haiku-4-5"


def test_automatic_model_selection_is_a_valid_node_override():
    node = NodeSpec(
        id="writer",
        type="TransformAgent",
        config={"model": "claude-haiku-4-5"},
        selected_model="auto",
        model_routing={
            "accuracy_priority": "maximum",
            "max_estimated_cost_usd": 0.25,
        },
    )

    assert node.effective_config()["model"] == "auto"
    assert node.model_routing.accuracy_priority == "maximum"


async def test_variables_and_output_projection_are_runtime_features():
    spec = load_workflow_from_string(
        """
name: Projection Test
static_variables:
  - name: policy
    type: text
    value: grounded-only
inputs:
  message:
    type: text
nodes:
  - id: echo
    type: Echo
    config:
      template: "{{variables.policy}}: {{inputs.message}}"
edges: []
output:
  include_input: true
  nodes:
    - node_id: echo
      flatten: true
"""
    )

    result = await run_workflow(spec, {"message": "hello"})

    assert result["state"]["variables"] == {"policy": "grounded-only"}
    assert result["output"] == {
        "input": {"message": "hello"},
        "text": "grounded-only: hello",
    }


async def test_outputs_namespace_is_a_deterministic_node_output_alias():
    spec = load_workflow_from_string(
        """
name: Output Namespace
nodes:
  - id: seed
    type: Literal
    config:
      value: exact
  - id: echo
    type: Echo
    config:
      template: "{{outputs.seed.value}}"
edges:
  - from: seed
    to: echo
entry: seed
exit: echo
"""
    )

    result = await run_workflow(spec, {})

    assert result["state"]["node_outputs"]["echo"]["text"] == "exact"


def test_namespaced_domain_state_uses_registered_reducer():
    namespace = "contract_test"

    def reducer(left, right):
        return {"total": left.get("total", 0) + right.get("total", 0)}

    DomainStateRegistry.register(namespace, reducer)
    merged = merge_domain_state(
        {namespace: {"total": 2}, "sales": {"accounts": {"A": 1}}},
        {namespace: {"total": 3}, "sales": {"accounts": {"B": 2}}},
    )

    assert merged[namespace] == {"total": 5}
    assert merged["sales"]["accounts"] == {"A": 1, "B": 2}


def test_unknown_edge_target_is_rejected_early():
    with pytest.raises(ValidationError):
        load_workflow_from_string(
            """
name: Bad Graph
nodes:
  - id: first
    type: Literal
    config:
      value: ok
edges:
  - from: first
    to: missing
"""
        )


def _guided_experience_yaml() -> str:
    return """
name: Guided Experience Test
experience:
  goal: Produce a checked requirement matrix.
  stages:
    - id: understand
      display_name: Understand the request
      purpose: Confirm requirements and success criteria.
      node_ids: [map_requirements]
      visibility: standard

nodes:
  - id: map_requirements
    type: Literal
    config:
      value: ok
    experience:
      stage_id: understand
      display_name: Map the call requirements
      purpose: Identify what the final result must address.
      contribution: Guides evidence collection and compliance review.
      expected_output: A checked requirement matrix
      failure_message: This step could not finish; completed work remains safe.
      visibility: standard
      show_agent_role: false
edges: []
"""


def test_guided_experience_round_trips_through_workflow_spec():
    spec = load_workflow_from_string(_guided_experience_yaml())

    assert spec.experience is not None
    assert spec.experience.goal == "Produce a checked requirement matrix."
    assert spec.experience.stages[0].id == "understand"
    assert spec.experience.stages[0].node_ids == ["map_requirements"]

    node = spec.nodes[0]
    assert node.experience is not None
    assert node.experience.display_name == "Map the call requirements"
    assert node.experience.contribution == (
        "Guides evidence collection and compliance review."
    )


def test_legacy_workflow_without_experience_remains_valid():
    spec = load_workflow_from_string(
        """
name: Legacy Workflow
nodes:
  - id: only
    type: Literal
    config:
      value: ok
edges: []
"""
    )

    assert spec.experience is None
    assert spec.nodes[0].experience is None


def test_duplicate_guided_stage_ids_rejected():
    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate({
            "name": "Bad Stages",
            "experience": {
                "stages": [
                    {"id": "understand", "display_name": "Understand"},
                    {"id": "understand", "display_name": "Understand again"},
                ],
            },
            "nodes": [{"id": "only", "type": "Literal", "config": {"value": "ok"}}],
        })


def test_guided_stage_referencing_unknown_node_is_rejected():
    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate({
            "name": "Bad Stage Nodes",
            "experience": {
                "stages": [
                    {
                        "id": "understand",
                        "display_name": "Understand",
                        "node_ids": ["missing"],
                    },
                ],
            },
            "nodes": [{"id": "only", "type": "Literal", "config": {"value": "ok"}}],
        })


def test_node_referencing_unknown_guided_stage_is_rejected():
    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate({
            "name": "Bad Node Stage",
            "experience": {
                "stages": [
                    {"id": "understand", "display_name": "Understand"},
                ],
            },
            "nodes": [{
                "id": "only",
                "type": "Literal",
                "config": {"value": "ok"},
                "experience": {"stage_id": "does_not_exist"},
            }],
        })


def test_library_metadata_round_trips_through_workflow_spec():
    spec = load_workflow_from_string(
        """
name: Library Metadata Test
library:
  title: Horizon Europe Part B Proposal
  summary: Create a researched, reviewed and cited Part B proposal.
  purpose: [evidence, research, drafting]
  suitable_for: [project-administrator, domain-expert]
  not_suitable_for: [financial-submission-forms]
  outputs: [proposal-drafts, docx, pdf]
  input_types: [documents, urls]
  typical_duration:
    minimum_minutes: 45
    maximum_minutes: 60
  human_reviews:
    count: 2
    labels: ["Approve draft package", "Approve final documents"]
  evidence_policy:
    drafting_requires_verified_evidence: true
    deep_research_is_context_only: true
  visibility_status: approved
  owner_team: Eurskem Research
nodes:
  - id: only
    type: Literal
    config:
      value: ok
edges: []
"""
    )

    assert spec.library is not None
    assert spec.library.title == "Horizon Europe Part B Proposal"
    assert spec.library.purpose == ["evidence", "research", "drafting"]
    assert spec.library.typical_duration.minimum_minutes == 45
    assert spec.library.human_reviews.count == 2
    assert spec.library.evidence_policy.drafting_requires_verified_evidence is True
    assert spec.library.visibility_status == "approved"


def test_legacy_workflow_without_library_metadata_remains_valid():
    spec = load_workflow_from_string(
        """
name: Legacy Workflow
nodes:
  - id: only
    type: Literal
    config:
      value: ok
edges: []
"""
    )

    assert spec.library is None


def test_library_metadata_defaults_visibility_status_to_draft():
    spec = WorkflowSpec.model_validate({
        "name": "Defaults Test",
        "library": {"title": "Untitled"},
        "nodes": [{"id": "only", "type": "Literal", "config": {"value": "ok"}}],
    })

    assert spec.library.visibility_status == "draft"
    assert spec.library.purpose == []
