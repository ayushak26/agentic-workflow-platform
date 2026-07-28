"""Independent two-provider Horizon Europe proposal evaluation panel."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field

from app.llm.catalog import provider_for_model
from app.proposal_graph.coverage import build_call_coverage_matrix
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import EvidenceStance

HORIZON_EVALUATOR_PROMPT_VERSION = "horizon-partb-v1"
HORIZON_CRITERIA = ("excellence", "impact", "implementation")

_RUBRICS = {
    "excellence": (
        "Evaluate clarity and pertinence of objectives; ambition beyond the "
        "state of the art; soundness of the methodology; interdisciplinarity, "
        "open science, and relevant gender dimension. Check whether advances "
        "are evidenced rather than merely asserted."
    ),
    "impact": (
        "Evaluate credibility of pathways to the topic outcomes and wider "
        "impacts; scale and significance; barriers and assumptions; and the "
        "quality of dissemination, exploitation, communication, and uptake."
    ),
    "implementation": (
        "Evaluate work-plan quality, risk management, resources, consortium "
        "capacity, role complementarity, effort, governance, and whether tasks, "
        "deliverables, milestones, owners, and timing form a credible plan."
    ),
}


class HorizonJudgeVerdict(BaseModel):
    score: float = Field(ge=0.0, le=5.0)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    reasoning: str


class HorizonJudgeResult(HorizonJudgeVerdict):
    criterion: str
    evaluator_model: str


class HorizonCriterionPanel(BaseModel):
    criterion: str
    mean_score: float
    disagreement: float
    judge_results: list[HorizonJudgeResult]


class HorizonEvaluationReport(BaseModel):
    prompt_version: str
    generator_model: str | None = None
    evaluator_models: list[str]
    criteria: list[HorizonCriterionPanel]
    total_score: float
    threshold_passed: bool
    coverage_percent: float
    deterministic_blockers: list[str] = Field(default_factory=list)
    high_disagreement_criteria: list[str] = Field(default_factory=list)


def _provider(model: str) -> str:
    try:
        return provider_for_model(model)
    except ValueError:
        return model.split("-", 1)[0]


def validate_independent_models(
    evaluator_models: list[str],
    generator_model: str | None,
) -> None:
    if len(evaluator_models) != 2 or len(set(evaluator_models)) != 2:
        raise ValueError("exactly two different evaluator models are required")
    if len({_provider(model) for model in evaluator_models}) != 2:
        raise ValueError(
            "independent evaluation requires judges from two different providers"
        )
    if generator_model and generator_model in evaluator_models:
        raise ValueError(
            "an evaluator model cannot be the exact model that generated the draft"
        )


def deterministic_horizon_blockers(graph: ProposalGraph) -> tuple[float, list[str]]:
    matrix = build_call_coverage_matrix(graph)
    blockers = [
        f"Call requirement {item} is not fully covered."
        for item in matrix.blocking_requirement_ids
    ]

    contradicted_claims = []
    unverified_claims = []
    for claim in graph.claims.values():
        relations = [
            graph.claim_evidence[item]
            for item in claim.evidence_relation_ids
            if item in graph.claim_evidence
        ]
        if any(
            relation.stance == EvidenceStance.CONTRADICTS
            and relation.confidence >= 0.72
            for relation in relations
        ):
            contradicted_claims.append(claim.id)
        if claim.evidence_source_ids and not any(
            relation.stance == EvidenceStance.SUPPORTS
            and relation.confidence >= 0.72
            for relation in relations
        ):
            unverified_claims.append(claim.id)

    if contradicted_claims:
        blockers.append(
            "Contradicted claims must be resolved: "
            + ", ".join(contradicted_claims)
        )
    if unverified_claims:
        blockers.append(
            "Claims have sources but no verified supporting passage: "
            + ", ".join(unverified_claims)
        )
    return matrix.coverage_percent, blockers


async def evaluate_horizon_proposal(
    llm: Any,
    *,
    proposal_text: str,
    graph: ProposalGraph,
    generator_model: str | None,
    evaluator_models: list[str],
    criterion_threshold: float = 3.0,
    total_threshold: float = 10.0,
) -> HorizonEvaluationReport:
    validate_independent_models(evaluator_models, generator_model)
    if not proposal_text.strip():
        raise ValueError("proposal_text cannot be empty")

    coverage_percent, deterministic_blockers = deterministic_horizon_blockers(
        graph
    )
    evidence_summary = {
        "call_coverage_percent": coverage_percent,
        "verified_relations": [
            item.model_dump(mode="json")
            for item in graph.claim_evidence.values()
        ],
        "object_counts": {
            "requirements": len(graph.call_requirements),
            "objectives": len(graph.objectives),
            "outcomes": len(graph.outcomes),
            "work_packages": len(graph.work_packages),
            "partners": len(graph.partners),
            "kpis": len(graph.kpis),
        },
    }

    async def judge(model: str, criterion: str) -> HorizonJudgeResult:
        verdict = await llm.complete_structured(
            model=model,
            system=(
                "Act as an independent Horizon Europe Part B evaluator. Score "
                "only the named criterion from 0 to 5. Identify concrete "
                "strengths and weaknesses; do not reward claims that lack "
                "traceable evidence. You are blind to the other evaluator."
            ),
            user=(
                f"CRITERION: {criterion.upper()}\n"
                f"RUBRIC: {_RUBRICS[criterion]}\n\n"
                f"DETERMINISTIC FACTS:\n"
                f"{json.dumps(evidence_summary, ensure_ascii=False)}\n\n"
                f"PROPOSAL:\n{proposal_text}"
            ),
            response_model=HorizonJudgeVerdict,
            temperature=0.0,
            max_tokens=1800,
        )
        return HorizonJudgeResult(
            criterion=criterion,
            evaluator_model=model,
            **verdict.model_dump(),
        )

    calls = [
        judge(model, criterion)
        for criterion in HORIZON_CRITERIA
        for model in evaluator_models
    ]
    results = list(await asyncio.gather(*calls))

    panels: list[HorizonCriterionPanel] = []
    for criterion in HORIZON_CRITERIA:
        criterion_results = [
            item for item in results if item.criterion == criterion
        ]
        scores = [item.score for item in criterion_results]
        panels.append(
            HorizonCriterionPanel(
                criterion=criterion,
                mean_score=round(sum(scores) / len(scores), 2),
                disagreement=round(max(scores) - min(scores), 2),
                judge_results=criterion_results,
            )
        )

    total = round(sum(item.mean_score for item in panels), 2)
    high_disagreement = [
        item.criterion for item in panels if item.disagreement >= 1.0
    ]
    threshold_passed = (
        not deterministic_blockers
        and total >= total_threshold
        and all(item.mean_score >= criterion_threshold for item in panels)
    )
    return HorizonEvaluationReport(
        prompt_version=HORIZON_EVALUATOR_PROMPT_VERSION,
        generator_model=generator_model,
        evaluator_models=evaluator_models,
        criteria=panels,
        total_score=total,
        threshold_passed=threshold_passed,
        coverage_percent=coverage_percent,
        deterministic_blockers=deterministic_blockers,
        high_disagreement_criteria=high_disagreement,
    )
