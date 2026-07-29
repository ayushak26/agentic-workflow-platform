"""TransformAgent: pure LLM transform with structured output."""
from __future__ import annotations

import json
from typing import Annotated, Any, Type

from pydantic import BaseModel, BeforeValidator, Field, create_model

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
    max_tokens: int = Field(default=16384, ge=256)
    max_retries: int = Field(default=1, ge=0, le=3)


class TransformInput(BaseModel):
    pass


class TransformOutput(BaseModel):
    raw: str
    parsed: dict[str, Any]


def _decode_json_container(value: Any) -> Any:
    """Decode a JSON object or array returned inside a string.

    Some models occasionally return a nested tool argument like:

        {
            "ssh": "{\"status\":\"PARTIAL\",\"gaps\":[]}"
        }

    The outer response is valid, but ssh is still a string. This validator
    converts it back into a dictionary before final Pydantic validation.
    """
    if not isinstance(value, str):
        return value

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


JsonStringList = Annotated[
    list[str],
    BeforeValidator(_decode_json_container),
]

JsonObject = Annotated[
    dict[str, Any],
    BeforeValidator(_decode_json_container),
]


_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "list": JsonStringList,
    "dict": JsonObject,
    "object": JsonObject,
}


def _build_response_model(
    name: str,
    schema: dict[str, str],
) -> Type[BaseModel]:
    fields: dict[str, Any] = {}

    for key, type_str in schema.items():
        py_type = _TYPE_MAP.get(type_str.lower(), str)
        fields[key] = (py_type, ...)

    return create_model(
        name,
        **fields,
    )  # type: ignore[call-overload]


@NodeRegistry.register
class TransformAgent(NodeType):
    type_name = "TransformAgent"
    description = (
        "Pure LLM transform: summarize, classify, rewrite, extract."
    )
    input_schema = TransformInput
    output_schema = TransformOutput
    config_schema = TransformConfig

    async def run(
        self,
        state,
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        llm = self.services["llm"]
        cfg = TransformConfig(**resolved_config)
        system = cfg.system_prompt or ""

        if not cfg.output_schema:
            response = await llm.complete(
                model=cfg.model,
                system=system,
                user=cfg.prompt_template,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )

            return {
                "raw": response.text,
                "parsed": {},
            }

        response_model = _build_response_model(
            f"{self.node_id}_Output",
            cfg.output_schema,
        )

        prompt = cfg.prompt_template
        last_error: Exception | None = None
        total_attempts = cfg.max_retries + 1

        for attempt in range(1, total_attempts + 1):
            try:
                instance = await llm.complete_structured(
                    model=cfg.model,
                    system=system,
                    user=prompt,
                    response_model=response_model,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                )

                parsed = instance.model_dump(mode="python")

                return {
                    "raw": json.dumps(
                        parsed,
                        ensure_ascii=False,
                    ),
                    "parsed": parsed,
                }

            except Exception as error:
                last_error = error

                log.warning(
                    "transform.structured_attempt_failed",
                    node_id=self.node_id,
                    attempt=attempt,
                    total_attempts=total_attempts,
                    error=str(error),
                )

                if attempt < total_attempts:
                    prompt = (
                        f"{cfg.prompt_template}\n\n"
                        "CORRECTION REQUIRED: The previous structured "
                        "response did not match the required schema. "
                        "Return every field using its native JSON type. "
                        "Return objects as JSON objects and lists as JSON "
                        "arrays. Do not put JSON inside quoted strings.\n"
                        f"Validation error: {error}"
                    )

        raise RuntimeError(
            f"TransformAgent '{self.node_id}' failed to produce valid "
            f"structured output after {total_attempts} attempt(s): "
            f"{last_error}"
        ) from last_error