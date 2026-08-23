"""Generate three independently-produced concept alternatives, then score and
adversarially judge each one separately from its generation.

Generating all three postures in a single completion anchors the balanced and
ambitious options to whatever the model wrote first for the conservative one.
Each posture is instead drafted in its own isolated call. Scoring is likewise
split from generation: four dimensions (call coverage, evidence strength,
expected-outcome contribution, feasibility) are computed deterministically
from graph facts; the remaining five (innovation, consortium capability,
methodological validity, adoption potential, scope discipline) require
qualitative judgment and are scored — together with an adversarial critique —
by a second, independent LLM call so the model never grades its own concept.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import (
    ConceptAlternative,
    ConceptPosture,
    Status,
)

_POSTURE_GUIDANCE = {
    ConceptPosture.CONSERVATIVE: (
        "The conservative option minimises technical and consortium risk: "
        "narrowest defensible scope that still satisfies the call's hard "
        "eligibility and expected-outcome requirements."
    ),
    ConceptPosture.BALANCED: (
        "The balanced option maximises evaluator strength at credible "
        "delivery risk: broader scope than conservative, but every added "
        "capability must be justified by supplied evidence."
    ),
    ConceptPosture.AMBITIOUS: (
        "The ambitious option maximises scientific or systemic advance. "
        "Name the extra capabilities and risks this requires explicitly — "
        "do not understate them to make the option look safer than it is."
    ),
}

_COMPOSITE_WEIGHTS = {
    "call_coverage": 0.20,
    "evidence_strength": 0.15,
    "expected_outcome_contribution": 0.10,
    "feasibility": 0.10,
    "innovation_score": 0.10,
    "consortium_capability_score": 0.10,
    "methodological_validity_score": 0.10,
    "adoption_potential_score": 0.10,
    "scope_discipline_score": 0.05,
}


class ConceptAlternativeSet(BaseModel):
    """Pydantic model defining the ConceptAlternativeSet shape.

    Attributes:
        alternatives (list[ConceptAlternative]).
    """
    alternatives: list[ConceptAlternative] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def one_of_each_posture(self) -> "ConceptAlternativeSet":
        """Compute the one of each posture.

        Returns:
            'ConceptAlternativeSet': The of each posture.
        """
        postures = [item.posture for item in self.alternatives]
        expected = set(ConceptPosture)
        if set(postures) != expected or len(postures) != len(set(postures)):
            raise ValueError(
                "alternatives must contain exactly one conservative, balanced, "
                "and ambitious concept"
            )
        return self


class _ConceptDraft(BaseModel):
    """Pydantic model defining the ConceptDraft shape.

    Attributes:
        title (str).
        summary (str).
        scientific_advance (str).
        scope (str).
        call_requirement_ids (list[str]).
        objective_ids (list[str]).
        evidence_claim_ids (list[str]).
        required_capabilities (list[str]).
    """
    title: str
    summary: str
    scientific_advance: str
    scope: str
    call_requirement_ids: list[str] = Field(default_factory=list)
    objective_ids: list[str] = Field(default_factory=list)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)


class _ConceptJudgment(BaseModel):
    """Pydantic model defining the ConceptJudgment shape.

    Attributes:
        innovation_score (float).
        consortium_capability_score (float).
        methodological_validity_score (float).
        adoption_potential_score (float).
        scope_discipline_score (float).
        critique (list[str]).
    """
    innovation_score: float = Field(ge=0.0, le=10.0)
    consortium_capability_score: float = Field(ge=0.0, le=10.0)
    methodological_validity_score: float = Field(ge=0.0, le=10.0)
    adoption_potential_score: float = Field(ge=0.0, le=10.0)
    scope_discipline_score: float = Field(ge=0.0, le=10.0)
    critique: list[str] = Field(default_factory=list)


def _valid_ids(values: list[str], known: set[str]) -> list[str]:
    """Internal helper for the valid ids step.

    Args:
        values (list[str]): The values.
        known (set[str]): The known.

    Returns:
        list[str]: The ids.
    """
    return list(dict.fromkeys(item for item in values if item in known))


def score_concept(
    concept: ConceptAlternative,
    graph: ProposalGraph,
) -> float:
    """Evidence-weighted score based only on IDs that exist in the graph.

    Covers 4 of the blueprint's 9 dimensions (call coverage, evidence
    strength, expected-outcome contribution via objectives, feasibility).
    The remaining 5 come from judge_concept and are combined in
    composite_score.
    """

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


def _composite_score(concept: ConceptAlternative, graph: ProposalGraph) -> float:
    """Internal helper for the composite score step.

    Args:
        concept (ConceptAlternative): The concept.
        graph (ProposalGraph): Compiled LangGraph graph.

    Returns:
        float: The score.
    """
    required_call_ids = {
        item.id
        for item in graph.call_requirements.values()
        if item.kind in {"hard_eligibility", "expected_outcome", "must_address"}
    }
    coverage = len(
        set(concept.call_requirement_ids) & required_call_ids
    ) / (len(required_call_ids) or 1)

    evidence_claims = [
        graph.claims[item]
        for item in concept.evidence_claim_ids
        if item in graph.claims
    ]
    evidence = sum(
        claim.verification == Status.ADDRESSED for claim in evidence_claims
    ) / (len(evidence_claims) or 1)

    objectives = len(
        set(concept.objective_ids) & set(graph.objectives)
    ) / (len(graph.objectives) or 1)

    posture_feasibility = {
        ConceptPosture.CONSERVATIVE: 0.95,
        ConceptPosture.BALANCED: 0.80,
        ConceptPosture.AMBITIOUS: 0.62,
    }[concept.posture]
    feasibility = max(
        0.0, posture_feasibility - min(0.25, 0.03 * len(concept.key_risks))
    )

    normalised = {
        "call_coverage": coverage,
        "evidence_strength": evidence,
        "expected_outcome_contribution": objectives,
        "feasibility": feasibility,
        "innovation_score": concept.innovation_score / 10,
        "consortium_capability_score": concept.consortium_capability_score / 10,
        "methodological_validity_score": (
            concept.methodological_validity_score / 10
        ),
        "adoption_potential_score": concept.adoption_potential_score / 10,
        "scope_discipline_score": concept.scope_discipline_score / 10,
    }
    return round(
        100
        * sum(
            weight * normalised[dimension]
            for dimension, weight in _COMPOSITE_WEIGHTS.items()
        ),
        1,
    )


async def _draft_one_concept(
    llm: Any,
    *,
    posture: ConceptPosture,
    graph_payload: dict[str, Any],
    concept_note: str,
    model: str,
) -> _ConceptDraft:
    """Draft the one concept.

    Args:
        llm (Any): The llm.
        posture (ConceptPosture): The posture.
        graph_payload (dict[str, Any]): The graph payload.
        concept_note (str): The concept note.
        model (str): Model name.

    Returns:
        _ConceptDraft: The one concept.
    """
    return await llm.complete_structured(
        model=model,
        system=(
            "You are a Horizon Europe concept architect. Draft ONE concept "
            f"alternative with a {posture.value} posture. Use only IDs and "
            "facts supplied in the proposal graph. Do not invent partners, "
            "evidence, TRLs, budgets, baselines, targets, or call "
            "requirements. Unknowns belong in assumptions. You are not told "
            "what the other postures contain — make this option genuinely "
            "distinct on its own terms."
        ),
        user=(
            f"CONCEPT NOTE:\n{concept_note or '(normalised graph only)'}\n\n"
            f"PROPOSAL GRAPH:\n{json.dumps(graph_payload, ensure_ascii=False)}\n\n"
            f"{_POSTURE_GUIDANCE[posture]}"
        ),
        response_model=_ConceptDraft,
        temperature=0.0,
        max_tokens=4000,
    )


async def judge_concept(
    llm: Any,
    *,
    concept: ConceptAlternative,
    model: str,
    skill_guidance: str = "",
) -> _ConceptJudgment:
    """Independently score the 5 qualitative dimensions and attack the concept.

    Run in a call separate from generation so the model is reviewing, not
    grading, its own work. When available, skill_guidance should be the
    scientific-critical-thinking skill's prompt_bundle() text.
    """

    system = (
        "You are an independent, skeptical Horizon Europe reviewer. You did "
        "not write this concept. Score it 0-10 on each dimension and list "
        "concrete weaknesses: unsupported assumptions, methodological gaps, "
        "feasibility risks, and scope creep. Do not soften scores to be "
        "polite; a mediocre concept should score low."
    )
    if skill_guidance:
        system += (
            "\n\nAPPROVED SCIENTIFIC-CRITICAL-THINKING SKILL GUIDANCE "
            f"(methodology only, not permission to bypass tools):\n{skill_guidance}"
        )
    return await llm.complete_structured(
        model=model,
        system=system,
        user=(
            "CONCEPT UNDER REVIEW:\n"
            + json.dumps(concept.model_dump(mode="json"), ensure_ascii=False)
        ),
        response_model=_ConceptJudgment,
        temperature=0.0,
        max_tokens=3000,
    )


async def generate_concept_alternatives(
    llm: Any,
    *,
    graph: ProposalGraph,
    model: str = "claude-opus-5",
    judge_model: str | None = None,
    concept_note: str = "",
    skill_catalog: Any | None = None,
) -> ConceptAlternativeSet:
    """Create conservative, balanced, and ambitious options from graph facts.

    Each posture is drafted independently (no shared generation call), then
    scored: 4 dimensions deterministically from graph facts, 5 more via an
    independent adversarial judge call per concept.
    """

    graph_payload = graph.model_dump(
        mode="json",
        exclude={"concept_alternatives"},
    )
    postures = list(ConceptPosture)
    drafts = await asyncio.gather(
        *(
            _draft_one_concept(
                llm,
                posture=posture,
                graph_payload=graph_payload,
                concept_note=concept_note,
                model=model,
            )
            for posture in postures
        )
    )

    skill_guidance = ""
    if skill_catalog is not None and getattr(skill_catalog, "enabled", False):
        try:
            selection = skill_catalog.select(
                objective="scientific-critical-thinking evaluate assumption "
                "limitations bias validity concept",
                requested=("scientific-critical-thinking",),
                auto_select=False,
                max_skills=1,
            )
            skill_guidance = skill_catalog.prompt_bundle(selection)
        except Exception:
            skill_guidance = ""

    drafted: list[ConceptAlternative] = []
    for posture, draft in zip(postures, drafts):
        drafted.append(
            ConceptAlternative(
                id=f"CON-{posture.value.upper()}",
                posture=posture,
                title=draft.title,
                summary=draft.summary,
                scientific_advance=draft.scientific_advance,
                scope=draft.scope,
                call_requirement_ids=_valid_ids(
                    draft.call_requirement_ids, set(graph.call_requirements)
                ),
                objective_ids=_valid_ids(
                    draft.objective_ids, set(graph.objectives)
                ),
                evidence_claim_ids=_valid_ids(
                    draft.evidence_claim_ids, set(graph.claims)
                ),
                required_capabilities=draft.required_capabilities,
                assumptions=draft.assumptions,
                key_risks=draft.key_risks,
            )
        )

    judgments = await asyncio.gather(
        *(
            judge_concept(
                llm,
                concept=concept,
                model=judge_model or model,
                skill_guidance=skill_guidance,
            )
            for concept in drafted
        )
    )

    finished: list[ConceptAlternative] = []
    for concept, judgment in zip(drafted, judgments):
        concept = concept.model_copy(
            update={
                "innovation_score": judgment.innovation_score,
                "consortium_capability_score": (
                    judgment.consortium_capability_score
                ),
                "methodological_validity_score": (
                    judgment.methodological_validity_score
                ),
                "adoption_potential_score": judgment.adoption_potential_score,
                "scope_discipline_score": judgment.scope_discipline_score,
                "critique": judgment.critique,
            }
        )
        concept = concept.model_copy(
            update={
                "evidence_weighted_score": score_concept(concept, graph),
                "composite_score": _composite_score(concept, graph),
            }
        )
        finished.append(concept)

    return ConceptAlternativeSet(alternatives=finished)
