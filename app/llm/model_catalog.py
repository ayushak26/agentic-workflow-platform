"""Provider-neutral LLM catalog used by routing, validation, cost, and UI.

The catalog keeps model identifiers and operational traits in one place.
``strengths`` are routing hints, not benchmark claims. Production teams can
override the hints with node-specific evaluation scores in ``model_routing``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.llm.openai_registry import OPENAI_MODEL_REGISTRY


AUTO_MODEL = "auto"
AUTO_MODEL_LABEL = "Best possible LLM (Auto)"

ModelTier = Literal["economy", "standard", "premium"]
ProviderName = Literal[
    "anthropic",
    "openai",
    "moonshot-local",
    "zai-local",
]


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: ProviderName
    tier: ModelTier
    speed_rank: int
    strengths: frozenset[str]
    input_usd_per_1k: float
    output_usd_per_1k: float


MODEL_PROFILES: tuple[ModelProfile, ...] = (
    *(
        ModelProfile(
            name=model.name,
            provider="openai",
            tier=model.tier,
            speed_rank=model.speed_rank,
            strengths=model.strengths,
            input_usd_per_1k=model.input_usd_per_1k,
            output_usd_per_1k=model.output_usd_per_1k,
        )
        for model in OPENAI_MODEL_REGISTRY
        if model.kind == "llm"
    ),
)

MODEL_PROFILE_BY_NAME = {profile.name: profile for profile in MODEL_PROFILES}
DEFAULT_LLM_MODELS = [profile.name for profile in MODEL_PROFILES]
MODEL_SELECTION_OPTIONS = [AUTO_MODEL, *DEFAULT_LLM_MODELS]
MODEL_OPTION_LABELS = {
    AUTO_MODEL: AUTO_MODEL_LABEL,
    **{
        model.name: model.display_name
        for model in OPENAI_MODEL_REGISTRY
        if model.kind == "llm"
    },
}
MODEL_PRICING = {
    profile.name: (
        profile.input_usd_per_1k,
        profile.output_usd_per_1k,
    )
    for profile in MODEL_PROFILES
}


def estimate_model_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate request cost using the platform's configured price catalog."""

    p_in, p_out = MODEL_PRICING.get(model, (0.005, 0.015))
    return round(
        (max(0, input_tokens) * p_in + max(0, output_tokens) * p_out)
        / 1000,
        6,
    )
