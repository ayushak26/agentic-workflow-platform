from __future__ import annotations

import pytest
from pydantic import ValidationError

import app.nodes  # noqa: F401
from app.runtime.domain_state import DomainStateRegistry, merge_domain_state
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.schema import NodeSpec


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
        config={
            "model": "claude-haiku-4-5",
        },
        selected_model="gpt-5.6-sol",
    )

    assert (
        node.effective_config()["model"]
        == "gpt-5.6-sol"
    )

    assert (
        node.config["model"]
        == "claude-haiku-4-5"
    )


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