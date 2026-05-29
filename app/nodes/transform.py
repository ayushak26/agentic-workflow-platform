"""TransformAgent: pure LLM transform with structured output.

Most nodes in the flagship workflow are TransformAgents. Examples: RFP
Intelligence (extract requirements + industry from raw RFP text), Section
Drafting (write one section given retrievals + brief), Compile and QA
(stitch sections, verify completeness)."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)


class TransformConfig(BaseModel):
    model: str = "claude-sonnet-4-5"
    prompt_template: str                        # already-resolved by runtime
    system_prompt: str | None = None
    output_schema: dict[str, str] = Field(default_factory=dict)
    # e.g. {"requirements": "list", "industry": "str"}
    temperature: float = 0.2
    max_retries: int = 1


class TransformInput(BaseModel):
    """Inputs are referenced via {{...}} templating; we don't validate
    a fixed shape here — the prompt_template encodes the contract."""
    pass


class TransformOutput(BaseModel):
    """The output dict shape is dynamic per node. We carry the raw text
    and the parsed JSON; downstream templating reaches into parsed."""
    raw: str
    parsed: dict[str, Any]


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

        # If the caller declared an output_schema, append JSON instructions
        # to the user prompt. Cross-provider compatible (we don't depend on
        # provider-specific JSON-mode APIs).
        user_prompt = cfg.prompt_template
        if cfg.output_schema:
            schema_hint = json.dumps(cfg.output_schema)
            user_prompt += (
                f"\n\nRespond ONLY with valid JSON matching this schema:\n{schema_hint}"
                "\nDo not include any prose before or after the JSON."
            )

        messages = [{"role": "user", "content": user_prompt}]

        for attempt in range(cfg.max_retries + 1):
            raw = await llm.chat(
                model=cfg.model,
                messages=messages,
                system=cfg.system_prompt,
                temperature=cfg.temperature,
            )
            if not cfg.output_schema:
                return {"raw": raw, "parsed": {}}

            try:
                parsed = json.loads(_strip_json_fences(raw))
                # Light validation: declared keys must be present
                missing = [k for k in cfg.output_schema if k not in parsed]
                if missing:
                    raise ValidationError.from_exception_data(
                        "TransformOutput",
                        [{"type": "missing", "loc": (k,)} for k in missing],
                    )
                return {"raw": raw, "parsed": parsed}
            except (json.JSONDecodeError, ValidationError) as e:
                log.warning(
                    "transform.parse_failed",
                    node_id=self.node_id, attempt=attempt, error=str(e),
                )
                if attempt == cfg.max_retries:
                    # Final attempt failed — return raw with empty parsed.
                    # Downstream nodes that depend on parsed.field will fail
                    # loudly via the templating resolver's KeyError.
                    return {"raw": raw, "parsed": {}}
                # Retry with an explicit correction in the next message
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content":
                    f"That wasn't valid JSON for the schema. Return ONLY a JSON object "
                    f"matching: {json.dumps(cfg.output_schema)}"})

        # Unreachable
        return {"raw": "", "parsed": {}}


def _strip_json_fences(text: str) -> str:
    """LLMs sometimes wrap JSON in ```json ... ``` despite instructions."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text.strip()