"""Deterministic freeze step for Human Gate 3 ("select and freeze the concept").

ConceptAlternativesAgent proposes three independently-generated, scored, and
adversarially-judged alternatives plus a recommended_concept_id (highest
composite_score). A HumanInLoopAgent gate lets a human accept that
recommendation or overwrite it with another alternative's id. This node reads
that gate's decision, resolves it against the three alternatives with no
LLM call, and fails closed if the decision doesn't match a known id — a typo
in the human's edit must never silently fall back to a default.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.evidence.models import coerce_typed_list_field
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.models import ConceptAlternative


class ConceptFreezeInput(BaseModel):
    pass


class ConceptFreezeConfig(BaseModel):
    alternatives: str | list[ConceptAlternative]
    selected_concept_id: str

    @field_validator("alternatives", mode="before")
    @classmethod
    def _coerce_alternatives(cls, value: Any) -> Any:
        return coerce_typed_list_field(value, ConceptAlternative, "alternatives")


class ConceptFreezeOutput(BaseModel):
    selected_concept: ConceptAlternative


@NodeRegistry.register
class ConceptFreezeAgent(NodeType):
    type_name = "ConceptFreezeAgent"
    description = (
        "Resolve the human gate's concept decision against the three "
        "generated alternatives. Fails closed on an unrecognised id — no "
        "LLM call, no default fallback."
    )
    input_schema = ConceptFreezeInput
    config_schema = ConceptFreezeConfig
    output_schema = ConceptFreezeOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = ConceptFreezeConfig(**resolved_config)
        if isinstance(cfg.alternatives, str):
            raise ValueError(
                "alternatives template did not resolve to a concept list"
            )
        selected_id = cfg.selected_concept_id.strip()
        by_id = {item.id: item for item in cfg.alternatives}
        if selected_id not in by_id:
            raise ValueError(
                f"selected_concept_id {selected_id!r} does not match any "
                f"generated alternative ({sorted(by_id)}). Re-run the "
                "concept-selection gate with a valid id."
            )
        return ConceptFreezeOutput(
            selected_concept=by_id[selected_id]
        ).model_dump(mode="json")
