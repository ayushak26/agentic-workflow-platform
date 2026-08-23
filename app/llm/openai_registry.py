"""Authoritative OpenAI model registry for every platform capability.

Only text-generation models belong in the generic LLM router. Embedding,
image-generation, and Deep Research models are exposed through their own
task-specific services so an endpoint-incompatible model can never be chosen
for a workflow call.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


OpenAIModelKind = Literal[
    "llm",
    "embedding",
    "image",
    "deep_research",
]
OpenAIModelTier = Literal["economy", "standard", "premium", "specialized"]


@dataclass(frozen=True)
class OpenAIModelDefinition:
    """Provides the OpenAIModelDefinition behaviour.

    Attributes:
        name (str).
        display_name (str).
        kind (OpenAIModelKind).
        tier (OpenAIModelTier).
        strengths (frozenset[str]).
        speed_rank (int).
        input_usd_per_1k (float).
        output_usd_per_1k (float).
    """
    name: str
    display_name: str
    kind: OpenAIModelKind
    tier: OpenAIModelTier
    strengths: frozenset[str] = frozenset()
    speed_rank: int = 1
    input_usd_per_1k: float = 0.0
    output_usd_per_1k: float = 0.0
    tool_calling: bool = False
    structured_output: bool = False
    image_input: bool = False
    reasoning_efforts: tuple[str, ...] = ()
    deprecated: bool = False
    snapshot_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Compute the as dict.

        Returns:
            dict[str, Any]: The dict.
        """
        value = asdict(self)
        value["strengths"] = sorted(self.strengths)
        value["reasoning_efforts"] = list(self.reasoning_efforts)
        return value


# Prices are USD per 1K tokens and are used only for routing estimates and
# cost telemetry. Image-generation pricing is metric-based rather than token
# based, so image entries intentionally keep token prices at zero.
OPENAI_MODEL_REGISTRY: tuple[OpenAIModelDefinition, ...] = (
    OpenAIModelDefinition(
        name="gpt-5.6-sol",
        display_name="GPT-5.6 Sol",
        kind="llm",
        tier="premium",
        strengths=frozenset(
            {
                "writing",
                "reasoning",
                "structured",
                "tool_use",
                "coding",
                "long_context",
            }
        ),
        speed_rank=1,
        input_usd_per_1k=0.005,
        output_usd_per_1k=0.030,
        tool_calling=True,
        structured_output=True,
        image_input=True,
        reasoning_efforts=("none", "low", "medium", "high", "xhigh"),
    ),
    OpenAIModelDefinition(
        name="gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        kind="llm",
        tier="standard",
        strengths=frozenset(
            {
                "writing",
                "reasoning",
                "structured",
                "tool_use",
                "general",
                "long_context",
            }
        ),
        speed_rank=3,
        input_usd_per_1k=0.002,
        output_usd_per_1k=0.012,
        tool_calling=True,
        structured_output=True,
        image_input=True,
        reasoning_efforts=("none", "low", "medium", "high", "xhigh"),
    ),
    OpenAIModelDefinition(
        name="gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        kind="llm",
        tier="economy",
        strengths=frozenset(
            {
                "classification",
                "extraction",
                "summarization",
                "structured",
                "tool_use",
                "general",
            }
        ),
        speed_rank=5,
        input_usd_per_1k=0.0002,
        output_usd_per_1k=0.0012,
        tool_calling=True,
        structured_output=True,
        image_input=True,
        reasoning_efforts=("none", "low", "medium", "high"),
    ),
    OpenAIModelDefinition(
        name="gpt-5",
        display_name="GPT-5",
        kind="llm",
        tier="standard",
        strengths=frozenset(
            {"reasoning", "structured", "tool_use", "general"}
        ),
        speed_rank=2,
        input_usd_per_1k=0.00125,
        output_usd_per_1k=0.010,
        tool_calling=True,
        structured_output=True,
        image_input=True,
        reasoning_efforts=("minimal", "low", "medium", "high"),
    ),
    OpenAIModelDefinition(
        name="gpt-5-mini",
        display_name="GPT-5 Mini",
        kind="llm",
        tier="economy",
        strengths=frozenset(
            {
                "classification",
                "extraction",
                "summarization",
                "structured",
                "tool_use",
                "general",
            }
        ),
        speed_rank=4,
        input_usd_per_1k=0.00025,
        output_usd_per_1k=0.002,
        tool_calling=True,
        structured_output=True,
        image_input=True,
        reasoning_efforts=("minimal", "low", "medium", "high"),
    ),
    OpenAIModelDefinition(
        name="gpt-4o-mini",
        display_name="GPT-4o Mini",
        kind="llm",
        tier="economy",
        strengths=frozenset(
            {
                "classification",
                "extraction",
                "summarization",
                "structured",
                "tool_use",
                "general",
            }
        ),
        speed_rank=6,
        input_usd_per_1k=0.00015,
        output_usd_per_1k=0.0006,
        tool_calling=True,
        structured_output=True,
        image_input=True,
    ),
    OpenAIModelDefinition(
        name="text-embedding-3-small",
        display_name="Text Embedding 3 Small",
        kind="embedding",
        tier="specialized",
        strengths=frozenset({"embedding", "retrieval"}),
        speed_rank=2,
        input_usd_per_1k=0.00002,
    ),
    OpenAIModelDefinition(
        name="text-embedding-ada-002",
        display_name="Text Embedding Ada 002",
        kind="embedding",
        tier="specialized",
        strengths=frozenset({"embedding", "retrieval", "compatibility"}),
        speed_rank=1,
        input_usd_per_1k=0.0001,
        deprecated=True,
    ),
    OpenAIModelDefinition(
        name="gpt-image-2-2026-04-21",
        display_name="GPT Image 2 (2026-04-21 snapshot)",
        kind="image",
        tier="specialized",
        strengths=frozenset(
            {"image_generation", "image_editing", "high_fidelity"}
        ),
        speed_rank=1,
        image_input=True,
        snapshot_of="gpt-image-2",
    ),
    OpenAIModelDefinition(
        name="chatgpt-image-latest",
        display_name="ChatGPT Image Latest",
        kind="image",
        tier="specialized",
        strengths=frozenset({"image_generation", "compatibility"}),
        speed_rank=2,
        image_input=True,
        deprecated=True,
    ),
    OpenAIModelDefinition(
        name="dall-e-2",
        display_name="DALL-E 2",
        kind="image",
        tier="specialized",
        strengths=frozenset({"image_generation", "legacy"}),
        speed_rank=3,
        deprecated=True,
    ),
)

OPENAI_MODEL_BY_NAME = {
    definition.name: definition for definition in OPENAI_MODEL_REGISTRY
}
OPENAI_MODEL_NAMES = tuple(OPENAI_MODEL_BY_NAME)


def models_for_kind(kind: OpenAIModelKind) -> tuple[str, ...]:
    """Compute the models for kind.

    Args:
        kind (OpenAIModelKind): The kind.

    Returns:
        tuple[str, ...]: The for kind.
    """
    return tuple(
        definition.name
        for definition in OPENAI_MODEL_REGISTRY
        if definition.kind == kind
    )


OPENAI_LLM_MODEL_NAMES = models_for_kind("llm")
OPENAI_EMBEDDING_MODEL_NAMES = models_for_kind("embedding")
OPENAI_IMAGE_MODEL_NAMES = models_for_kind("image")
OPENAI_DEEP_RESEARCH_MODEL_NAMES = models_for_kind("deep_research")


# Ordered degradation chains preserve endpoint capability. Task-specific
# registries below never cross into a different endpoint family.
OPENAI_LLM_FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "gpt-5.6-sol": (
        "gpt-5.6-terra",
        "gpt-5",
        "gpt-5.6-luna",
        "gpt-5-mini",
        "gpt-4o-mini",
    ),
    "gpt-5.6-terra": (
        "gpt-5",
        "gpt-5.6-luna",
        "gpt-5-mini",
        "gpt-4o-mini",
    ),
    "gpt-5.6-luna": (
        "gpt-5-mini",
        "gpt-4o-mini",
        "gpt-5.6-terra",
    ),
    "gpt-5": (
        "gpt-5.6-terra",
        "gpt-5-mini",
        "gpt-4o-mini",
    ),
    "gpt-5-mini": (
        "gpt-5.6-luna",
        "gpt-4o-mini",
        "gpt-5.6-terra",
    ),
    "gpt-4o-mini": (
        "gpt-5.6-luna",
        "gpt-5-mini",
        "gpt-5.6-terra",
    ),
}

OPENAI_IMAGE_FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "gpt-image-2-2026-04-21": (
        "chatgpt-image-latest",
        "dall-e-2",
    ),
    "chatgpt-image-latest": (
        "gpt-image-2-2026-04-21",
        "dall-e-2",
    ),
    "dall-e-2": (
        "gpt-image-2-2026-04-21",
        "chatgpt-image-latest",
    ),
}

OPENAI_EMBEDDING_FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "text-embedding-3-small": ("text-embedding-ada-002",),
    "text-embedding-ada-002": ("text-embedding-3-small",),
}

def openai_model(model: str) -> OpenAIModelDefinition:
    """Compute the openai model.

    Args:
        model (str): Model name.

    Returns:
        OpenAIModelDefinition: The model.
    """
    try:
        return OPENAI_MODEL_BY_NAME[model]
    except KeyError as exc:
        raise ValueError(
            f"OpenAI model {model!r} is not in the approved registry"
        ) from exc


def require_openai_model_kind(model: str, kind: OpenAIModelKind) -> str:
    """Compute the require openai model kind.

    Args:
        model (str): Model name.
        kind (OpenAIModelKind): The kind.

    Returns:
        str: The openai model kind.
    """
    definition = openai_model(model)
    if definition.kind != kind:
        raise ValueError(
            f"OpenAI model {model!r} is registered for "
            f"{definition.kind!r}, not {kind!r}"
        )
    return model
