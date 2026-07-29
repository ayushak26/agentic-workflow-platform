"""Provider-neutral LLM catalog used by routing, validation, cost, and UI.

The catalog keeps model identifiers and operational traits in one place.
``strengths`` are routing hints, not benchmark claims. Production teams can
override the hints with node-specific evaluation scores in ``model_routing``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    ModelProfile(
        name="claude-opus-5",
        provider="anthropic",
        tier="premium",
        speed_rank=1,
        strengths=frozenset({"writing", "reasoning", "long_context"}),
        input_usd_per_1k=0.005,
        output_usd_per_1k=0.025,
    ),
    ModelProfile(
        name="claude-sonnet-4-5",
        provider="anthropic",
        tier="standard",
        speed_rank=2,
        strengths=frozenset({"writing", "reasoning", "general"}),
        input_usd_per_1k=0.003,
        output_usd_per_1k=0.015,
    ),
    ModelProfile(
        name="claude-haiku-4-5",
        provider="anthropic",
        tier="economy",
        speed_rank=3,
        strengths=frozenset({"classification", "extraction", "summarization"}),
        input_usd_per_1k=0.00025,
        output_usd_per_1k=0.00125,
    ),
    ModelProfile(
        name="gpt-5.6-sol",
        provider="openai",
        tier="premium",
        speed_rank=1,
        strengths=frozenset(
            {"reasoning", "structured", "tool_use", "coding"}
        ),
        input_usd_per_1k=0.005,
        output_usd_per_1k=0.030,
    ),
    ModelProfile(
        name="gpt-5",
        provider="openai",
        tier="standard",
        speed_rank=2,
        strengths=frozenset(
            {"reasoning", "structured", "tool_use", "general"}
        ),
        input_usd_per_1k=0.005,
        output_usd_per_1k=0.020,
    ),
    ModelProfile(
        name="gpt-5-mini",
        provider="openai",
        tier="economy",
        speed_rank=3,
        strengths=frozenset(
            {"classification", "extraction", "structured", "tool_use"}
        ),
        input_usd_per_1k=0.0005,
        output_usd_per_1k=0.0015,
    ),
    ModelProfile(
        name="local-kimi-k3",
        provider="moonshot-local",
        tier="premium",
        speed_rank=1,
        strengths=frozenset(
            {"writing", "reasoning", "tool_use", "long_context"}
        ),
        # API-metered cost is zero. Infrastructure cost is accounted for
        # separately by the private deployment.
        input_usd_per_1k=0.0,
        output_usd_per_1k=0.0,
    ),
    ModelProfile(
        name="local-glm-5",
        provider="zai-local",
        tier="premium",
        speed_rank=1,
        strengths=frozenset(
            {"reasoning", "structured", "tool_use", "coding"}
        ),
        input_usd_per_1k=0.0,
        output_usd_per_1k=0.0,
    ),
)

MODEL_PROFILE_BY_NAME = {profile.name: profile for profile in MODEL_PROFILES}
DEFAULT_LLM_MODELS = [profile.name for profile in MODEL_PROFILES]
MODEL_SELECTION_OPTIONS = [AUTO_MODEL, *DEFAULT_LLM_MODELS]
MODEL_OPTION_LABELS = {
    AUTO_MODEL: AUTO_MODEL_LABEL,
    **{model: model for model in DEFAULT_LLM_MODELS},
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
