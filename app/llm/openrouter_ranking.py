"""Auto-router config for OpenRouter — see docs/architecture/LLM_MODEL_ROUTING.md.

When a node routes through OpenRouter with automatic model selection, Eurskem does NOT build
its own ranking — OpenRouter's own auto-router (https://openrouter.ai/docs/features/
model-routing) already does real, data-driven cost/quality routing with fallback, using
platform-wide usage signals no local heuristic could replicate. Building a parallel scorer
here would also mean fabricating "accuracy"/"faithfulness" numbers with no ground truth (see
git history on this file for the earlier, abandoned attempt) — OpenRouter's router needs none
of that; it also already honors the account's own provider/ZDR/guardrail restrictions, unlike
several models this session picked by hand and had rejected.

This module's only job is translating Eurskem's existing `ModelRoutingPolicy`
(app/runtime/schema.py) into OpenRouter's `plugins: [{id: "auto-router", ...}]` request shape:

    {
      "model": "openrouter/auto",
      "plugins": [{"id": "auto-router", "cost_tier": "medium", "allowed_models": [...]}]
    }

`allowed_models`/`excluded_models` are populated ONLY from real, operator-supplied data — a
node's `quality_scores`/`faithfulness_scores` (per-model observations, e.g. from a past
Eval Lab Scorecard) narrow the candidate pool to models Eurskem has actually validated well
for this workflow, when such data exists. With no observed data, the field is left unset and
OpenRouter's router chooses freely across its full catalog.
"""
from __future__ import annotations

from typing import Any, Mapping

# Real, documented OpenRouter router models (https://openrouter.ai/docs/features/
# model-routing) — auto/auto-beta/fusion/pareto-code/free/bodybuilder. Distinct from this
# codebase's own pre-existing `AUTO_MODEL` ("auto", app/llm/model_catalog.py — the static-
# catalog ModelRouter's sentinel). Both resolve to the SAME "auto" concept from a workflow
# author's point of view (there is deliberately only one "Auto" option in the Builder UI);
# app/llm/gateway.py decides whether that resolves to this OpenRouter path or the static
# catalog's ModelRouter based on which connection/combo the request is actually routed through.
DEFAULT_OPENROUTER_ROUTER_MODEL = "openrouter/auto"

_OBSERVED_SCORE_THRESHOLD = 0.7  # only a genuinely good observed score narrows the pool


def _cost_tier_for_priority(accuracy_priority: str) -> str:
    return {"economy": "low", "balanced": "medium", "maximum": "high"}.get(
        accuracy_priority, "medium"
    )


def build_auto_router_plugin(
    *,
    accuracy_priority: str = "balanced",
    max_estimated_cost_usd: float | None = None,
    quality_scores: Mapping[str, float] | None = None,
    faithfulness_scores: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Builds one `plugins[]` entry for OpenRouter's auto-router from Eurskem's
    ModelRoutingPolicy fields. `max_estimated_cost_usd` currently has no direct OpenRouter
    auto-router equivalent (cost_tier is a band, not a ceiling) — captured for a future
    per-call cost-ceiling check at the gateway layer, not applied here."""
    del max_estimated_cost_usd  # see docstring — not yet an auto-router parameter

    plugin: dict[str, Any] = {
        "id": "auto-router",
        "cost_tier": _cost_tier_for_priority(accuracy_priority),
    }

    well_observed = {
        model_id
        for scores in (quality_scores, faithfulness_scores)
        if scores
        for model_id, value in scores.items()
        if value >= _OBSERVED_SCORE_THRESHOLD
    }
    if well_observed:
        plugin["allowed_models"] = sorted(well_observed)

    return plugin


_ROUTER_SLUGS = frozenset({"auto", "auto-beta", "fusion", "pareto-code", "free", "bodybuilder"})


def is_openrouter_router_model(value: str) -> bool:
    """True for OpenRouter's own router meta-models (openrouter/auto, openrouter/free, ...).

    Deliberately does NOT gate on `app.llm.openrouter_catalog.is_openrouter_model_id`: that
    check requires a vendor/model split (openrouter/<vendor>/<model>) to validate REAL backing
    models, but router meta-models are single-segment (openrouter/auto, not
    openrouter/<vendor>/auto) — gating on it would reject every router model outright.
    """
    prefix = "openrouter/"
    if not value.startswith(prefix):
        return False
    slug = value[len(prefix) :]
    return slug in _ROUTER_SLUGS
