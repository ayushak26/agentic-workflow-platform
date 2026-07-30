"""TextAssemblerAgent: deterministic, non-LLM concatenation of pre-rendered
text parts into one document.

Asking an LLM to re-emit an already-drafted, multi-page document (e.g. to
"assemble" or "merge" several long sections into one) risks silent
truncation: the call is capped by max_tokens, and TransformAgent has no way
to detect a cut-off response when no output_schema is set. This node joins
already-generated text deterministically instead, so a long final document
can never lose content to a model's output-token ceiling.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class TextAssemblerConfig(BaseModel):
    parts: list[str] = Field(default_factory=list)
    separator: str = "\n\n"


class TextAssemblerInput(BaseModel):
    pass


class TextAssemblerOutput(BaseModel):
    text: str


@NodeRegistry.register
class TextAssemblerAgent(NodeType):
    type_name = "TextAssemblerAgent"
    description = (
        "Deterministically joins pre-rendered text parts with a separator - "
        "no LLM call, so the result is never truncated by a max_tokens "
        "ceiling. Use to assemble a long final document from chunks that "
        "were each already generated within a realistic token budget."
    )
    input_schema = TextAssemblerInput
    output_schema = TextAssemblerOutput
    config_schema = TextAssemblerConfig

    async def run(
        self,
        state: Any,
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = TextAssemblerConfig(**resolved_config)
        return {"text": cfg.separator.join(cfg.parts)}
