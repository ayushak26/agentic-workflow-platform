"""Generate three grounded concept alternatives and score them deterministically."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import (
    ConceptAlternative,
    ConceptPosture,
    Status,
)


class ConceptAlternativeSet(BaseModel):
    alternatives: list[ConceptAlternative] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def one_of_each_posture(self) -> "ConceptAlternativeSet":
        postures = [item.posture for item in self.alternatives]
        expected = set(ConceptPosture)
        if set(postures) != expected or len(postures) != len(set(postures)):
            raise ValueError(
                "alternatives must contain exactly one conservative, balanced, "
                "and ambitious concept"
            )
        return self


def _valid_ids(values: list[str], known: set[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item in known))


def score_concept(
    concept: ConceptAlternative,
    graph: ProposalGraph,
) -> float:
    """Evidence-weighted score based only on IDs that exist in the graph."""

    required_call_ids = {
        item.id
        for item in graph.call_requirements.values()
        if item.kind in {"hard_eligibility", "expected_outcome", "must_address"}
    }
    covered = set(concept.call_requirement_ids) & required_call_ids
    coverage = len(covered) / (len(required_call_ids) or 1)

    evidence_claims = [
        graph.claims[item]
        for item in concept.evidence_claim_ids
        if item in graph.claims
    ]
    verified = sum(
        claim.verification == Status.ADDRESSED
        for claim in evidence_claims
    )
    evidence = verified / (len(evidence_claims) or 1)

    objective_ids = set(concept.objective_ids) & set(graph.objectives)
    objectives = len(objective_ids) / (len(graph.objectives) or 1)

    posture_feasibility = {
        ConceptPosture.CONSERVATIVE: 0.95,
        ConceptPosture.BALANCED: 0.80,
        ConceptPosture.AMBITIOUS: 0.62,
    }[concept.posture]
    risk_penalty = min(0.25, 0.03 * len(concept.key_risks))
    feasibility = max(0.0, posture_feasibility - risk_penalty)

    return round(
        100
        * (
            0.40 * coverage
            + 0.30 * evidence
            + 0.15 * objectives
            + 0.15 * feasibility
        ),
        1,
    )


async def generate_concept_alternatives(
    llm: Any,
    *,
    graph: ProposalGraph,
    model: str = "claude-opus-5",
    concept_note: str = "",
) -> ConceptAlternativeSet:
    """Create conservative, balanced, and ambitious options from graph facts."""

    graph_payload = graph.model_dump(
        mode="json",
        exclude={
            "concept_alternatives",
        },
    )
    generated = await llm.complete_structured(
        model=model,
        system=(
            "You are a Horizon Europe concept architect. Generate exactly "
            "three genuinely different alternatives: conservative, balanced, "
            "and ambitious. Use only IDs and facts supplied in the proposal "
            "graph. Do not invent partners, evidence, TRLs, budgets, baselines, "
            "targets, or call requirements. Unknowns belong in assumptions."
        ),
        user=(
            f"CONCEPT NOTE:\n{concept_note or '(normalised graph only)'}\n\n"
            f"PROPOSAL GRAPH:\n{json.dumps(graph_payload, ensure_ascii=False)}\n\n"
            "The conservative option minimises technical and consortium risk. "
            "The balanced option should maximise evaluator strength at credible "
            "delivery risk. The ambitious option should maximise scientific or "
            "systemic advance while naming the extra capabilities and risks."
        ),
        response_model=ConceptAlternativeSet,
        temperature=0.0,
        max_tokens=7000,
    )

    clean: list[ConceptAlternative] = []
    for alternative in generated.alternatives:
        canonical_id = f"CON-{alternative.posture.value.upper()}"
        item = alternative.model_copy(
            update={
                "id": canonical_id,
                "call_requirement_ids": _valid_ids(
                    alternative.call_requirement_ids,
                    set(graph.call_requirements),
                ),
                "objective_ids": _valid_ids(
                    alternative.objective_ids,
                    set(graph.objectives),
                ),
                "evidence_claim_ids": _valid_ids(
                    alternative.evidence_claim_ids,
                    set(graph.claims),
                ),
            }
        )
        item = item.model_copy(
            update={"evidence_weighted_score": score_concept(item, graph)}
        )
        clean.append(item)
    return ConceptAlternativeSet(alternatives=clean)
