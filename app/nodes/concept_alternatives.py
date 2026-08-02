"""Workflow node for three grounded Horizon concept alternatives."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.concepts import (
    ConceptAlternativeSet,
    generate_concept_alternatives,
)
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.state import (
    proposal_graph_from_state,
    proposal_graph_state_update,
)


class ConceptAlternativesInput(BaseModel):
    pass


class ConceptAlternativesConfig(BaseModel):
    model: str = "claude-opus-5"
    judge_model: str | None = None
    concept_note: str = ""


class ConceptAlternativesOutput(ConceptAlternativeSet):
    # Highest composite_score alternative, offered as the editable default at
    # the "select and freeze the concept" human gate. The human can accept
    # it or overwrite it with another alternative's id before ConceptFreezeAgent
    # reads the gate's decision.
    recommended_concept_id: str = ""


@NodeRegistry.register
class ConceptAlternativesAgent(NodeType):
    type_name = "ConceptAlternativesAgent"
    description = (
        "Generate conservative, balanced, and ambitious Horizon concepts "
        "grounded in the approved proposal graph."
    )
    input_schema = ConceptAlternativesInput
    config_schema = ConceptAlternativesConfig
    output_schema = ConceptAlternativesOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"llm", "cost_ledger"}

    async def run(self, state: dict, resolved_config: dict) -> dict:
        cfg = ConceptAlternativesConfig(**resolved_config)
        llm = self.services.get("llm")
        if llm is None:
            raise RuntimeError("ConceptAlternativesAgent requires the llm service")
        graph = proposal_graph_from_state(state)
        result = await generate_concept_alternatives(
            llm,
            graph=graph,
            model=cfg.model,
            judge_model=cfg.judge_model,
            concept_note=cfg.concept_note,
            skill_catalog=self.services.get("scientific_skill_catalog"),
        )
        alternatives = {
            item.id: item for item in result.alternatives
        }
        recommended = max(
            result.alternatives, key=lambda item: item.composite_score
        )
        payload = ConceptAlternativesOutput(
            alternatives=result.alternatives,
            recommended_concept_id=recommended.id,
        ).model_dump()
        payload["__state__"] = proposal_graph_state_update(
            ProposalGraph(concept_alternatives=alternatives)
        )
        return payload
