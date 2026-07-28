from __future__ import annotations

from pathlib import Path

import app.nodes  # noqa: F401

from app.llm.anthropic_gw import _supports_temperature
from app.llm.openai_gw import _chat_tool_reasoning_effort
from app.llm.model_catalog import AUTO_MODEL, MODEL_SELECTION_OPTIONS
from app.llm.registry import _FALLBACK_MODEL
from app.nodes.registry import NodeRegistry
from app.observability.cost_ledger import CostLedger
from app.runtime.loader import load_workflow_from_string
from app.runtime.schema import DEFAULT_LLM_MODELS


def test_new_models_are_in_the_runtime_catalog():
    assert "claude-opus-5" in DEFAULT_LLM_MODELS
    assert "gpt-5.6-sol" in DEFAULT_LLM_MODELS


def test_builder_manifest_exposes_models_as_dropdown():
    transform = next(
        item
        for item in NodeRegistry.manifest()
        if item["type_name"] == "TransformAgent"
    )

    model_schema = transform["config_schema"]["properties"]["model"]

    assert model_schema["enum"] == MODEL_SELECTION_OPTIONS
    assert model_schema["enum"][0] == AUTO_MODEL
    assert model_schema["x-enum-labels"][AUTO_MODEL] == (
        "Best possible LLM (Auto)"
    )


def test_opus_5_falls_back_to_gpt_5_6_sol():
    assert (
        _FALLBACK_MODEL["claude-opus-5"]
        == "gpt-5.6-sol"
    )


def test_provider_compatibility_rules():
    assert _supports_temperature("claude-opus-5") is False
    assert (
        _chat_tool_reasoning_effort("gpt-5.6-sol")
        == "none"
    )
    assert _chat_tool_reasoning_effort("gpt-5") is None


def test_new_model_costs():
    assert (
        CostLedger.calculate(
            "claude-opus-5",
            1000,
            1000,
        )
        == 0.03
    )

    assert (
        CostLedger.calculate(
            "gpt-5.6-sol",
            1000,
            1000,
        )
        == 0.035
    )


def test_agro_thrive_uses_opus_5():
    path = Path("workflows/agro_thrive_partb.yaml")
    spec = load_workflow_from_string(path.read_text())

    selected = [
        node.config.get("model")
        for node in spec.nodes
        if node.config.get(
            "model",
            "",
        ).startswith("claude-opus-")
    ]

    assert selected
    assert set(selected) == {"claude-opus-5"}

    routed = [
        node
        for node in spec.nodes
        if node.config.get("model")
    ]
    assert routed
    assert all(node.selected_model == AUTO_MODEL for node in routed)
