"""RouterAgent rule-mode tests — no LLM needed.

LLM-mode tests use the stub_llm fixture (see test_router_llm_mode at bottom)."""
import pytest

import app.nodes  # noqa: F401
from app.nodes.registry import NodeRegistry


def _build_state(node_outputs: dict) -> dict:
    return {"node_outputs": node_outputs, "inputs": {}}


async def test_router_matches_first_rule():
    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "rule",
            "rules": [
                {"name": "finance",  "condition": "rfp_intel.parsed.industry == 'finance'"},
                {"name": "fallback", "default": True},
            ],
        },
    )
    state = _build_state({"rfp_intel": {"parsed": {"industry": "finance"}}})
    result = await node.run(state=state, resolved_config=node.config.model_dump())
    assert result["route"] == "finance"


async def test_router_falls_through_to_default():
    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "rule",
            "rules": [
                {"name": "finance",  "condition": "rfp_intel.parsed.industry == 'finance'"},
                {"name": "fallback", "default": True},
            ],
        },
    )
    state = _build_state({"rfp_intel": {"parsed": {"industry": "retail"}}})
    result = await node.run(state=state, resolved_config=node.config.model_dump())
    assert result["route"] == "fallback"


async def test_router_supports_numeric_comparison():
    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "rule",
            "rules": [
                {"name": "high", "condition": "score.value > 80"},
                {"name": "low",  "default": True},
            ],
        },
    )
    state = _build_state({"score": {"value": 91}})
    result = await node.run(state=state, resolved_config=node.config.model_dump())
    assert result["route"] == "high"


async def test_router_supports_boolean_comparison():
    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "rule",
            "rules": [
                {"name": "yes", "condition": "check.passed == True"},
                {"name": "no",  "default": True},
            ],
        },
    )
    state = _build_state({"check": {"passed": True}})
    result = await node.run(state=state, resolved_config=node.config.model_dump())
    assert result["route"] == "yes"


async def test_router_raises_without_default_when_nothing_matches():
    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "rule",
            "rules": [
                {"name": "finance", "condition": "x.y == 'finance'"},
            ],
        },
    )
    state = _build_state({"x": {"y": "retail"}})
    with pytest.raises(ValueError, match="matched no rule"):
        await node.run(state=state, resolved_config=node.config.model_dump())


async def test_router_missing_path_loses_the_comparison_instead_of_crashing():
    """An upstream lookup that legitimately found nothing (an unmatched
    customer, an empty MCP tool result) leaves later fields genuinely absent
    from state — a `mode: rule` condition referencing one of those fields
    must lose the comparison and fall through to the default route, not raise
    a KeyError/AttributeError three nodes into a run."""
    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "rule",
            "rules": [
                {"name": "named_owner", "condition": "lookup.first.owner_name != ''"},
                {"name": "queue", "default": True},
            ],
        },
    )
    # lookup.first is an empty dict — the shape an MCPToolAgent produces when
    # its underlying search found nothing — so `.owner_name` is missing, not
    # merely falsy.
    state = _build_state({"lookup": {"first": {}, "found": False}})
    result = await node.run(state=state, resolved_config=node.config.model_dump())
    assert result["route"] == "queue"


async def test_router_missing_path_on_a_wholly_absent_node_also_falls_through():
    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "rule",
            "rules": [
                {"name": "matched", "condition": "never_ran.parsed.field == 'x'"},
                {"name": "fallback", "default": True},
            ],
        },
    )
    state = _build_state({})
    result = await node.run(state=state, resolved_config=node.config.model_dump())
    assert result["route"] == "fallback"


async def test_router_llm_mode_returns_chosen_route(stub_llm):
    stub_llm.queue("finance")

    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "llm",
            "model": "claude-sonnet-4-5",
            "prompt": "Classify the industry",
            "context": "Some context",
            "rules": [
                {"name": "finance", "condition": None},
                {"name": "retail",  "condition": None},
                {"name": "other",   "default": True},
            ],
        },
        services={"llm": stub_llm},
    )
    state = _build_state({})
    result = await node.run(state=state, resolved_config=node.config.model_dump())
    assert result["route"] == "finance"


async def test_router_llm_mode_falls_back_on_invalid_choice(stub_llm):
    stub_llm.queue("nonsense_route_name")

    cls = NodeRegistry.get("RouterAgent")
    node = cls(
        node_id="r",
        raw_config={
            "mode": "llm",
            "model": "claude-sonnet-4-5",
            "prompt": "Classify",
            "context": "ctx",
            "rules": [
                {"name": "finance", "condition": None},
                {"name": "other",   "default": True},
            ],
        },
        services={"llm": stub_llm},
    )
    result = await node.run(state=_build_state({}), resolved_config=node.config.model_dump())
    assert result["route"] == "other"