"""Tests for app/llm/openrouter_ranking.py — translating Eurskem's ModelRoutingPolicy into
OpenRouter's native auto-router plugin config (no local scoring — see module docstring for
why that was deliberately abandoned in favor of OpenRouter's own router)."""
from __future__ import annotations

from app.llm.openrouter_ranking import (
    DEFAULT_OPENROUTER_ROUTER_MODEL,
    build_auto_router_plugin,
    is_openrouter_router_model,
)


def test_default_router_model_is_openrouter_auto():
    assert DEFAULT_OPENROUTER_ROUTER_MODEL == "openrouter/auto"


def test_is_openrouter_router_model_matches_known_router_variants():
    for slug in ("auto", "auto-beta", "fusion", "pareto-code", "free", "bodybuilder"):
        assert is_openrouter_router_model(f"openrouter/{slug}")


def test_is_openrouter_router_model_rejects_real_backing_models():
    assert not is_openrouter_router_model("openrouter/openai/gpt-4o-mini")
    assert not is_openrouter_router_model("openrouter/anthropic/claude-3-haiku")


def test_is_openrouter_router_model_rejects_non_openrouter_ids():
    assert not is_openrouter_router_model("gpt-4o-mini")
    assert not is_openrouter_router_model("auto")


def test_cost_tier_maps_from_accuracy_priority():
    assert build_auto_router_plugin(accuracy_priority="economy")["cost_tier"] == "low"
    assert build_auto_router_plugin(accuracy_priority="balanced")["cost_tier"] == "medium"
    assert build_auto_router_plugin(accuracy_priority="maximum")["cost_tier"] == "high"


def test_unknown_accuracy_priority_defaults_to_medium():
    assert build_auto_router_plugin(accuracy_priority="nonsense")["cost_tier"] == "medium"


def test_plugin_id_is_always_auto_router():
    assert build_auto_router_plugin()["id"] == "auto-router"


def test_no_allowed_models_when_no_observed_scores():
    plugin = build_auto_router_plugin()
    assert "allowed_models" not in plugin


def test_no_allowed_models_when_scores_are_below_threshold():
    plugin = build_auto_router_plugin(
        quality_scores={"openrouter/openai/gpt-4o-mini": 0.5},
        faithfulness_scores={"openrouter/anthropic/claude-3-haiku": 0.6},
    )
    assert "allowed_models" not in plugin


def test_allowed_models_includes_well_observed_quality_scores():
    plugin = build_auto_router_plugin(
        quality_scores={"openrouter/openai/gpt-4o-mini": 0.85}
    )
    assert plugin["allowed_models"] == ["openrouter/openai/gpt-4o-mini"]


def test_allowed_models_includes_well_observed_faithfulness_scores():
    plugin = build_auto_router_plugin(
        faithfulness_scores={"openrouter/anthropic/claude-3-haiku": 0.9}
    )
    assert plugin["allowed_models"] == ["openrouter/anthropic/claude-3-haiku"]


def test_allowed_models_merges_and_dedupes_both_sources_sorted():
    plugin = build_auto_router_plugin(
        quality_scores={"openrouter/openai/gpt-4o-mini": 0.9},
        faithfulness_scores={
            "openrouter/anthropic/claude-3-haiku": 0.95,
            "openrouter/openai/gpt-4o-mini": 0.99,  # same model, both sources agree — no dup
        },
    )
    assert plugin["allowed_models"] == [
        "openrouter/anthropic/claude-3-haiku",
        "openrouter/openai/gpt-4o-mini",
    ]


def test_max_estimated_cost_usd_accepted_but_not_applied():
    # No OpenRouter auto-router equivalent exists yet (cost_tier is a band, not a ceiling) —
    # documented in the module docstring. Must not raise or leak into the plugin dict.
    plugin = build_auto_router_plugin(max_estimated_cost_usd=0.01)
    assert "max_estimated_cost_usd" not in plugin
    assert "max_estimated_cost" not in plugin
