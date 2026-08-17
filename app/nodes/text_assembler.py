"""TextAssemblerAgent: this platform's business-facing Join node.

A join is only reachable once every branch it depends on has actually run —
`parts` are `{{node.field}}` template references, and the executor cannot
resolve any of them until the referenced upstream node has produced that
field. So a node listing several upstream branches in `parts` is already an
implicit "wait for all of these" join; this class turns that into an
explicit, deterministic combine step (no LLM call, so a long final document
can never lose content to a model's max_tokens ceiling — the original reason
this node exists, e.g. assembling a multi-page proposal from
already-drafted sections).

Kept as `TextAssemblerAgent` (not renamed) so every existing instance —
Horizon's `draft_review_packet`/`compile_v1`/`final_revision` among them —
keeps working with the exact same fields and output unchanged; `part_count`/
`all_parts_present` are additive.
"""
from __future__ import annotations

from typing import Any, ClassVar

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
    #: How many parts were actually combined — the join's "count".
    part_count: int = 0
    #: Whether every configured part resolved to a non-empty value. False
    #: means at least one upstream branch this join depends on produced
    #: nothing (or wasn't reached), without failing the run outright — a
    #: downstream Decision/Router can gate on this the same way it gates on
    #: any other node's `found`/`status`.
    all_parts_present: bool = True


@NodeRegistry.register
class TextAssemblerAgent(NodeType):
    type_name = "TextAssemblerAgent"
    description = (
        "Join: waits for multiple upstream branches (e.g. from a Parallel "
        "Split or Multi-Route) to complete, then deterministically combines "
        "their results — no LLM call, so a long final document can never be "
        "truncated by a max_tokens ceiling the way re-asking a model to "
        "re-emit it would be."
    )
    input_schema = TextAssemblerInput
    output_schema = TextAssemblerOutput
    config_schema = TextAssemblerConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "deterministic"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Combines the outputs of several upstream branches into one "
            "result once all of them have run — the business 'Join' step "
            "after a Parallel Split or Multi-Route."
        ),
        "why": (
            "A join is naturally deterministic: it does not decide anything, "
            "it waits for what already happened and lines it up. No model "
            "call means no truncation risk on a long combined document."
        ),
        "receives": "One templated reference per branch being joined.",
        "produces": "text (the combined result), part_count, all_parts_present.",
        "uses_ai": False,
        "external_action": False,
    }

    async def run(
        self,
        state: Any,
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = TextAssemblerConfig(**resolved_config)
        return {
            "text": cfg.separator.join(cfg.parts),
            "part_count": len(cfg.parts),
            "all_parts_present": all(part.strip() for part in cfg.parts),
        }
