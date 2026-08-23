"""Registry-wide conformance and generic edge-compatibility regression tests."""
from __future__ import annotations

from pydantic import BaseModel

import app.nodes  # noqa: F401
from app.nodes.base import NodeType
from app.nodes.contracts import DataType, check_compatibility
from app.nodes.registry import NodeRegistry
from app.runtime.preflight import preflight_workflow_yaml


def test_every_registered_node_has_a_complete_versioned_contract():
    manifests = NodeRegistry.manifest()
    assert manifests
    assert len(manifests) == len(NodeRegistry._registry)
    for manifest in manifests:
        contract = manifest["contract"]
        assert contract["version"] == "1"
        assert contract["type_name"] == manifest["type_name"]
        assert contract["accepts"]
        assert contract["produces"]
        assert set(contract["requires_capabilities"]) == set(
            NodeRegistry.get(manifest["type_name"]).required_services({})
        )


def test_all_directional_pairs_are_computable_without_exceptions():
    definitions = [node_type.definition({}) for node_type in NodeRegistry._registry.values()]
    checked = 0
    for source in definitions:
        for target in definitions:
            if source.type_name == target.type_name:
                continue
            result = check_compatibility(source, target)
            assert isinstance(result.compatible, bool)
            checked += 1
    assert checked == len(definitions) * (len(definitions) - 1)


class _Empty(BaseModel):
    pass


class _ImageOnly(NodeType):
    type_name = "_ImageOnly"
    input_schema = _Empty
    output_schema = _Empty
    config_schema = _Empty
    produces = {DataType.IMAGE}
    accepts = {DataType.STATE}

    async def run(self, state, resolved_config):
        return {}


class _TextOnly(NodeType):
    type_name = "_TextOnly"
    input_schema = _Empty
    output_schema = _Empty
    config_schema = _Empty
    accepts = {DataType.TEXT}
    produces = {DataType.TEXT}

    async def run(self, state, resolved_config):
        return {}


def test_incompatible_types_have_an_actionable_generic_error():
    result = check_compatibility(_ImageOnly.definition(), _TextOnly.definition())
    assert result.compatible is False
    assert result.issues[0].code == "EDGE_DATA_TYPE_INCOMPATIBLE"
    assert "image" in result.issues[0].message
    assert "text" in result.issues[0].message


def test_existing_shared_state_workflow_edges_remain_compatible():
    report = preflight_workflow_yaml("""
name: Existing state graph
nodes:
  - id: first
    type: Literal
    config: {value: hello}
  - id: second
    type: Echo
    config: {message: "{{outputs.first.value}}"}
edges:
  - from: first
    to: second
""")
    assert not [issue for issue in report.errors if issue.code.startswith("EDGE_")]
    assert any(check.name == "edge_compatibility" for check in report.checks)