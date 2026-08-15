from __future__ import annotations

import app.nodes  # noqa: F401 - populates the registry via discovery
from pydantic import BaseModel, Field

from app.nodes.about_synthesis import synthesize_about
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


def test_explicit_about_wins_over_synthesized_fields():
    """HumanInLoopAgent hand-authors `what`/`why`/`produces` — the merge in
    NodeRegistry._manifest_entry must never let a generic synthesized value
    clobber them."""
    entry = next(e for e in NodeRegistry.manifest() if e["type_name"] == "HumanInLoopAgent")
    about = entry["about"]
    assert about["what"].startswith("Pauses the run and waits for a person")
    assert "decision (approve/edit/reject)" in about["produces"]


def test_a_type_that_appears_in_real_workflows_gets_mined_neighbours():
    """RouterAgent shows up (as source or target) in several checked-in
    workflows — the adjacency miner should surface at least one real
    neighbour type, not an empty list."""
    entry = next(e for e in NodeRegistry.manifest() if e["type_name"] == "RouterAgent")
    about = entry["about"]
    assert about.get("typical_upstream") or about.get("typical_downstream")


def test_synthesis_never_invents_a_type_name_outside_the_registry():
    for entry in NodeRegistry.manifest():
        about = entry["about"]
        for neighbour in about.get("typical_upstream", []) + about.get("typical_downstream", []):
            assert neighbour in NodeRegistry._registry


class _DummyConfig(BaseModel):
    required_field: str
    optional_field: str = Field(default="", description="An optional field with a description.")


class _DummyIO(BaseModel):
    pass


def test_a_node_type_without_an_about_dict_gets_useful_generic_fields():
    dummy_type_name = "TestOnlyAboutSynthesisDummy"
    assert dummy_type_name not in NodeRegistry._registry

    try:
        @NodeRegistry.register
        class _DummyNode(NodeType):
            type_name = dummy_type_name
            description = "Does a dummy thing for this test only."
            input_schema = _DummyIO
            output_schema = _DummyIO
            config_schema = _DummyConfig

            async def run(self, state, resolved_config):
                return {}

        about = synthesize_about(_DummyNode)

        assert about["important_config"] == ["required_field"]
        assert "dummy thing" in about["when_to_use"]
        assert about["when_not_to_use"]
        # No real workflow uses this dummy type, so mined fields are absent
        # (never fabricated placeholders).
        assert "typical_upstream" not in about
        assert "typical_downstream" not in about
        assert "example" not in about

        # And the registry manifest actually carries it through the merge.
        entry = next(e for e in NodeRegistry.manifest() if e["type_name"] == dummy_type_name)
        assert entry["about"]["when_to_use"] == about["when_to_use"]
    finally:
        NodeRegistry._registry.pop(dummy_type_name, None)
