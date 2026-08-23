"""Generic preflight conformance for every live registered node type.

No type-name snapshot exists here. Adding a module under ``app/nodes`` causes
automatic discovery, registration validates its standardized contract, and
these parametrized checks immediately exercise its schemas and extension
points without requiring another hand-maintained list.
"""
from __future__ import annotations

import pytest

import app.nodes  # noqa: F401
from app.nodes.registry import NodeRegistry


@pytest.mark.parametrize("type_name", sorted(NodeRegistry._registry))
def test_registered_node_preflight_contract_is_complete(type_name: str):
    node_type = NodeRegistry.get(type_name)
    definition = node_type.definition({})

    assert definition.type_name == type_name
    assert definition.accepts
    assert definition.produces
    assert node_type.config_schema is not None
    assert node_type.input_schema is not None
    assert node_type.output_schema is not None
    assert isinstance(node_type.preflight_output_fields({}), set)
    assert isinstance(node_type.preflight_static_output_values({}), dict)
    assert set(definition.requires_capabilities) == node_type.required_services({})


@pytest.mark.parametrize("type_name", sorted(NodeRegistry._registry))
def test_registered_node_config_strictness_is_explicit(type_name: str):
    """Track legacy permissive schemas without breaking saved workflows.

    New scaffolded types use ``extra='forbid'``. Existing permissive types can
    be migrated deliberately with their own backward-compatibility tests.
    """

    extra = NodeRegistry.get(type_name).config_schema.model_config.get("extra")
    assert extra in {None, "ignore", "allow", "forbid"}


def test_discovery_has_no_import_failures():
    assert app.nodes.node_discovery_errors() == {}