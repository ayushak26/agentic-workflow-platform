"""Approved model catalog shared by the runtime, API, and Workflow Builder.

Local model names are stable platform aliases. The model identifier exposed by
vLLM or SGLang is deployment configuration and may differ from the alias.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelDefinition:
    """Provides the ModelDefinition behaviour.

    Attributes:
        name (str).
        display_name (str).
        provider (str).
        local (bool).
        tool_calling (bool).
        structured_output (bool).
        reasoning_efforts (tuple[str, ...]).
        platform_modalities (tuple[str, ...]).
    """
    name: str
    display_name: str
    provider: str
    local: bool = False
    tool_calling: bool = True
    structured_output: bool = True
    reasoning_efforts: tuple[str, ...] = ()
    platform_modalities: tuple[str, ...] = ("text",)
    upstream_url: str | None = None
    # Which data classes this model may process. Empty tuple = unrestricted
    # (all existing models keep prior behaviour). A non-empty tuple is an
    # allowlist enforced at the gateway boundary. The two hosted China-region
    # models are restricted to public data only — see docs/adr/ADR-0001.
    allowed_data_classes: tuple[str, ...] = ()

    def permits_data_class(self, data_class: str | None) -> bool:
        # No restriction configured -> allow anything (back-compat).
        """Compute the permits data class.

        Args:
            data_class (str | None): The data class.

        Returns:
            bool: The data class.
        """
        if not self.allowed_data_classes:
            return True
        # A caller that declares no data class cannot be proven safe against a
        # restricted model -> deny (fail closed).
        if data_class is None:
            return False
        return data_class in self.allowed_data_classes

    def as_dict(self) -> dict[str, Any]:
        """Compute the as dict.

        Returns:
            dict[str, Any]: The dict.
        """
        value = asdict(self)
        value["reasoning_efforts"] = list(self.reasoning_efforts)
        value["platform_modalities"] = list(self.platform_modalities)
        value["allowed_data_classes"] = list(self.allowed_data_classes)
        return value


MODEL_CATALOG: tuple[ModelDefinition, ...] = (
    ModelDefinition(
        name="claude-opus-5",
        display_name="Claude Opus 5",
        provider="anthropic",
    ),
    ModelDefinition(
        name="claude-sonnet-4-5",
        display_name="Claude Sonnet 4.5",
        provider="anthropic",
    ),
    ModelDefinition(
        name="claude-haiku-4-5",
        display_name="Claude Haiku 4.5",
        provider="anthropic",
    ),
    ModelDefinition(
        name="claude-fable-5",
        display_name="Claude Fable 5",
        provider="anthropic",
    ),
    ModelDefinition(
        name="gpt-5.6-sol",
        display_name="GPT-5.6 Sol",
        provider="openai",
    ),
    ModelDefinition(
        name="gpt-5",
        display_name="GPT-5",
        provider="openai",
    ),
    ModelDefinition(
        name="gpt-5-mini",
        display_name="GPT-5 Mini",
        provider="openai",
    ),
    ModelDefinition(
        name="gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        provider="openai",
    ),
    ModelDefinition(
        name="gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        provider="openai",
    ),
    ModelDefinition(
        name="gpt-4o-mini",
        display_name="GPT-4o Mini",
        provider="openai",
    ),
    ModelDefinition(
        name="local-kimi-k3",
        display_name="Kimi K3 (Local)",
        provider="moonshot-local",
        local=True,
        reasoning_efforts=("low", "high", "max"),
        upstream_url="https://github.com/MoonshotAI/Kimi-K3",
        allowed_data_classes=("public",),
    ),
    ModelDefinition(
        name="local-glm-5",
        display_name="GLM-5 (Local)",
        provider="zai-local",
        local=True,
        reasoning_efforts=("high", "max"),
        upstream_url="https://github.com/zai-org/GLM-5",
        allowed_data_classes=("public",),
    ),
)

MODEL_BY_NAME = {item.name: item for item in MODEL_CATALOG}
MODEL_NAMES = tuple(item.name for item in MODEL_CATALOG)
LOCAL_MODEL_NAMES = tuple(item.name for item in MODEL_CATALOG if item.local)


def model_definition(model: str) -> ModelDefinition:
    """Compute the model definition.

    Args:
        model (str): Model name.

    Returns:
        ModelDefinition: The definition.
    """
    try:
        return MODEL_BY_NAME[model]
    except KeyError as exc:
        raise ValueError(f"Model {model!r} is not in the approved catalog") from exc


def provider_for_model(model: str) -> str:
    """Compute the provider for model.

    Args:
        model (str): Model name.

    Returns:
        str: The for model.
    """
    return model_definition(model).provider


def is_local_model(model: str) -> bool:
    """Return whether local model.

    Args:
        model (str): Model name.

    Returns:
        bool: True when local model.
    """
    item = MODEL_BY_NAME.get(model)
    return bool(item and item.local)


def local_service_name(model: str) -> str | None:
    """Compute the local service name.

    Args:
        model (str): Model name.

    Returns:
        str | None: The service name.
    """
    return f"llm:{model}" if is_local_model(model) else None