"""TransformAgent: pure LLM transform with structured output."""
from __future__ import annotations

import json
from typing import Any, Type

from pydantic import BaseModel, Field, create_model

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)


class TransformConfig(BaseModel):
    model: str = "claude-sonnet-4-5"
    prompt_template: str
    system_prompt: str | None = None
    output_schema: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.2
    max_retries: int = 1


class TransformInput(BaseModel):
    pass


class TransformOutput(BaseModel):
    raw: str
    parsed: dict[str, Any]


# Map the YAML type strings to Python types for dynamic model building.
# list defaults to list[str] because OpenAI strict structured output requires
# an item type; bare `list` produces an invalid schema for that provider.
_TYPE_MAP: dict[str, Any] = {
    "str": str, "string": str,
    "int": int, "integer": int,
    "float": float, "number": float,
    "bool": bool, "boolean": bool,
    "list": list[str],
    "dict": dict[str, Any], "object": dict[str, Any],
}


def _build_response_model(name: str, schema: dict[str, str]) -> Type[BaseModel]:
    """Turn a YAML output_schema dict into a Pydantic model so the gateway's
    complete_structured can use the provider's native structured-output mode."""
    fields: dict[str, Any] = {}
    for key, type_str in schema.items():
        py_type = _TYPE_MAP.get(type_str.lower(), str)  # unknown → str
        fields[key] = (py_type, ...)  # required
    return create_model(name, **fields)  # type: ignore[call-overload]


@NodeRegistry.register
class TransformAgent(NodeType):
    type_name = "TransformAgent"
    description = "Pure LLM transform: summarize, classify, rewrite, extract."
    input_schema = TransformInput
    output_schema = TransformOutput
    config_schema = TransformConfig

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        llm = self.services["llm"]
        cfg = TransformConfig(**resolved_config)
        system = cfg.system_prompt or ""

        # Free-text transform (summarize, rewrite) — no schema declared.
        if not cfg.output_schema:
            resp = await llm.complete(
                model=cfg.model,
                system=system,
                user=cfg.prompt_template,
                temperature=cfg.temperature,
            )
            return {"raw": resp.text, "parsed": {}}

        # Structured transform (extract, classify) — provider guarantees the shape.
        response_model = _build_response_model(
            f"{self.node_id}_Output", cfg.output_schema
        )
        try:
            instance = await llm.complete_structured(
                model=cfg.model,
                system=system,
                user=cfg.prompt_template,
                response_model=response_model,
                temperature=cfg.temperature,
            )
            parsed = instance.model_dump()
            return {"raw": json.dumps(parsed), "parsed": parsed}
        except Exception as e:
            # complete_structured failed even with the provider's structured mode.
            # Return empty parsed; downstream templating will fail loudly on missing keys.
            log.warning(
                "transform.structured_failed",
                node_id=self.node_id, error=str(e),
            )
            return {"raw": "", "parsed": {}}