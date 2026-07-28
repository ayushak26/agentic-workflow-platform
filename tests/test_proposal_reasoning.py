from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.proposal_graph.concepts import generate_concept_alternatives
from app.proposal_graph.coverage import build_call_coverage_matrix
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.horizon_evaluator import (
    evaluate_horizon_proposal,
    validate_independent_models,
)
from app.proposal_graph.models import (
    CallRequirement,
    Claim,
    EvidenceRelation,
    EvidenceStance,
    Objective,
    Outcome,
    Status,
)
from app.runtime.loader import load_workflow_from_string


def covered_graph() -> ProposalGraph:
    relation = EvidenceRelation(
        id="EV-1",
        claim_id="CL-1",
        source_id="SRC-1",
        source_version_id="SRC-1:v1:abc",
        passage="The method improved accuracy.",
        locator="p. 4",
        passage_sha256="abc",
        stance=EvidenceStance.SUPPORTS,
        confidence=0.9,
        reason="Directly stated.",
        verifier_model="claude-sonnet-4-5",
        verified_at="2026-07-27T00:00:00+00:00",
    )
    return ProposalGraph(
        call_requirements={
            "CR-EO1": CallRequirement(
                id="CR-EO1",
                text="Deliver improved decision support.",
                kind="expected_outcome",
                addressed_by_section="2.1",
                addressed_by_ids=["OUT-1"],
                evidence_claim_ids=["CL-1"],
            )
        },
        claims={
            "CL-1": Claim(
                id="CL-1",
                text="The method improved accuracy.",
                evidence_source_ids=["SRC-1"],
                evidence_relation_ids=["EV-1"],
                verification=Status.ADDRESSED,
            )
        },
        claim_evidence={"EV-1": relation},
        objectives={
            "OBJ-1": Objective(
                id="OBJ-1",
                text="Validate decision support.",
            )
        },
        outcomes={
            "OUT-1": Outcome(
                id="OUT-1",
                text="Improved decision support.",
                call_requirement_id="CR-EO1",
            )
        },
    )


def test_call_coverage_requires_mapping_section_and_verified_evidence():
    matrix = build_call_coverage_matrix(covered_graph())
    assert matrix.coverage_percent == 100
    assert matrix.rows[0].status == Status.ADDRESSED
    assert matrix.submission_blocked is False

    graph = covered_graph()
    graph.call_requirements["CR-EO1"].addressed_by_section = None
    matrix = build_call_coverage_matrix(graph)
    assert matrix.rows[0].status == Status.PARTIAL
    assert matrix.submission_blocked is True


def test_reference_eu_proposal_pipeline_loads_with_all_new_nodes():
    spec = load_workflow_from_string(
        Path("workflows/eu_proposal_evidence_pipeline.yaml").read_text()
    )
    assert {node.type for node in spec.nodes} >= {
        "ScholarlyCandidateDiscoveryAgent",
        "FullTextEvidenceAcquirer",
        "ProposalEvidenceFactoryAgent",
        "HorizonHTMLProposalRenderer",
        "CallCoverageMatrixAgent",
        "ConceptAlternativesAgent",
        "HorizonEvaluationAgent",
        "HumanInLoopAgent",
    }


@pytest.mark.asyncio
async def test_concept_generator_returns_three_scored_postures(stub_llm):
    stub_llm.queue(json.dumps({
        "alternatives": [
            {
                "id": "anything",
                "posture": "conservative",
                "title": "Focused validation",
                "summary": "Validate the core method.",
                "scientific_advance": "A robust validated workflow.",
                "scope": "One technical pathway.",
                "call_requirement_ids": ["CR-EO1"],
                "objective_ids": ["OBJ-1"],
                "evidence_claim_ids": ["CL-1"],
                "required_capabilities": [],
                "assumptions": [],
                "key_risks": [],
            },
            {
                "id": "anything",
                "posture": "balanced",
                "title": "Integrated validation",
                "summary": "Validate and demonstrate uptake.",
                "scientific_advance": "Integrated evidence and deployment.",
                "scope": "Technical and user validation.",
                "call_requirement_ids": ["CR-EO1"],
                "objective_ids": ["OBJ-1"],
                "evidence_claim_ids": ["CL-1"],
                "required_capabilities": ["living lab"],
                "assumptions": [],
                "key_risks": ["adoption"],
            },
            {
                "id": "anything",
                "posture": "ambitious",
                "title": "European-scale system",
                "summary": "Validate a wider systemic pathway.",
                "scientific_advance": "Cross-region optimisation.",
                "scope": "Multiple deployment contexts.",
                "call_requirement_ids": ["CR-EO1", "HALLUCINATED"],
                "objective_ids": ["OBJ-1"],
                "evidence_claim_ids": ["CL-1"],
                "required_capabilities": ["European pilots"],
                "assumptions": ["additional partners"],
                "key_risks": ["scale", "data access"],
            },
        ]
    }))
    result = await generate_concept_alternatives(
        stub_llm,
        graph=covered_graph(),
    )

    assert {item.posture.value for item in result.alternatives} == {
        "conservative",
        "balanced",
        "ambitious",
    }
    assert all(item.evidence_weighted_score > 0 for item in result.alternatives)
    assert all(
        "HALLUCINATED" not in item.call_requirement_ids
        for item in result.alternatives
    )


class HorizonLLM:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, *, model, response_model, **kwargs):
        self.calls.append((model, kwargs["user"]))
        return response_model(
            score=4.0,
            strengths=["Clear pathway."],
            weaknesses=[],
            recommendations=[],
            reasoning="Strong against the supplied rubric.",
        )


@pytest.mark.asyncio
async def test_horizon_panel_is_blind_two_provider_and_thresholded():
    llm = HorizonLLM()
    report = await evaluate_horizon_proposal(
        llm,
        proposal_text="A complete proposal draft.",
        graph=covered_graph(),
        generator_model="claude-opus-5",
        evaluator_models=["claude-sonnet-4-5", "gpt-5"],
    )
    assert len(llm.calls) == 6
    assert report.total_score == 12
    assert report.threshold_passed is True
    assert all(item.disagreement == 0 for item in report.criteria)


def test_horizon_panel_rejects_same_provider_judges():
    with pytest.raises(ValueError, match="Anthropic and one OpenAI"):
        validate_independent_models(
            ["claude-sonnet-4-5", "claude-haiku-4-5"],
            "claude-opus-5",
        )
