"""Approved model catalog shared by the runtime, API, and Workflow Builder.

Local model names are stable platform aliases. The model identifier exposed by
vLLM or SGLang is deployment configuration and may differ from the alias.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelDefinition:
    name: str
    display_name: str
    provider: str
    local: bool = False
    tool_calling: bool = True
    structured_output: bool = True
    reasoning_efforts: tuple[str, ...] = ()
    platform_modalities: tuple[str, ...] = ("text",)
    upstream_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasoning_efforts"] = list(self.reasoning_efforts)
        value["platform_modalities"] = list(self.platform_modalities)
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
        name="local-kimi-k3",
        display_name="Kimi K3 (Local)",
        provider="moonshot-local",
        local=True,
        reasoning_efforts=("low", "high", "max"),
        upstream_url="https://github.com/MoonshotAI/Kimi-K3",
    ),
    ModelDefinition(
        name="local-glm-5",
        display_name="GLM-5 (Local)",
        provider="zai-local",
        local=True,
        reasoning_efforts=("high", "max"),
        upstream_url="https://github.com/zai-org/GLM-5",
    ),
)

MODEL_BY_NAME = {item.name: item for item in MODEL_CATALOG}
MODEL_NAMES = tuple(item.name for item in MODEL_CATALOG)
LOCAL_MODEL_NAMES = tuple(item.name for item in MODEL_CATALOG if item.local)


def model_definition(model: str) -> ModelDefinition:
    try:
        return MODEL_BY_NAME[model]
    except KeyError as exc:
        raise ValueError(f"Model {model!r} is not in the approved catalog") from exc


def provider_for_model(model: str) -> str:
    return model_definition(model).provider


def is_local_model(model: str) -> bool:
    item = MODEL_BY_NAME.get(model)
    return bool(item and item.local)


def local_service_name(model: str) -> str | None:
    return f"llm:{model}" if is_local_model(model) else None
