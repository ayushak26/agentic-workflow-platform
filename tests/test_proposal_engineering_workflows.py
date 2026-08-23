from __future__ import annotations

from pathlib import Path

import pytest

from app.nodes.proposal_submission_gate import ProposalSubmissionGate
from app.runtime.loader import load_workflow
from app.runtime.preflight import preflight_workflow_yaml


# The monolithic horizon_partb_autonomous_docx.yaml and the monolithic
# horizon_proposal_hitl_pdf.yaml were both retired in favor of the staged
# Part B pipeline (see
# workflows/horizon_partb_{evidence,drafts,drafts_to_docx}.yaml and
# workflows/pipelines/horizon_partb.pipeline.yaml, its declared production
# path). The structural invariants those monolith tests protected — human
# review gates on the proposal path, explicit model selection for
# generation-heavy steps, and a zero-token clean preflight — are asserted
# here against the staged replacement instead.
STAGED_PARTB = [
    Path("workflows/horizon_partb_evidence.yaml"),
    Path("workflows/horizon_partb_drafts.yaml"),
    Path("workflows/horizon_partb_drafts_to_docx.yaml"),
]


def _types(path: Path) -> list[str]:
    return [node.type for node in load_workflow(path).nodes]


def test_staged_pipeline_preserves_human_review_gates():
    staged_counts = {path.stem: _types(path) for path in STAGED_PARTB}
    total_hitl = sum(
        types.count("HumanInLoopAgent") for types in staged_counts.values()
    )
    # The retired HITL monolith had four human gates; the staged pipeline
    # distributes at least that many across its stages (seven in evidence,
    # two in drafts, three in drafts_to_docx at the time of writing).
    assert total_hitl >= 4
    # Human review exists on every stage of the chain, not only the last.
    assert all(
        "HumanInLoopAgent" in types for types in staged_counts.values()
    )
    # The rendering stage offers both export families and still ends behind
    # a submission gate.
    render_types = staged_counts["horizon_partb_drafts_to_docx"]
    assert "HorizonHTMLProposalRenderer" in render_types
    assert "HorizonDOCXProposalRenderer" in render_types
    assert "ProposalSubmissionGate" in render_types


def test_staged_partb_declares_explicit_models_and_passes_preflight():
    generation_heavy = {
        "TransformAgent",
        "GraphNormalizer",
        "ScholarlyCandidateDiscoveryAgent",
        "ProposalEvidenceFactoryAgent",
        "ScientificSkillAgent",
        "ConceptAlternativesAgent",
    }
    for path in STAGED_PARTB:
        spec = load_workflow(path)
        llm_nodes = [node for node in spec.nodes if node.type in generation_heavy]
        # Every generation-heavy step states its model explicitly — the
        # staged workflows pin catalog models rather than relying on
        # defaults, so a silent catalog change cannot swap them.
        assert all(node.selected_model for node in llm_nodes)

        report = preflight_workflow_yaml(path.read_text(encoding="utf-8"))
        assert report.valid is True
        assert report.tokens_spent == 0
        assert report.errors == []


@pytest.mark.asyncio
async def test_submission_gate_fails_closed_on_missing_inputs_and_evidence():
    proposal = (
        "# 1 Excellence\n"
        + "Grounded content. " * 100
        + "\n# 2 Impact\n"
        + "Impact content. " * 100
        + "\n# 3 Implementation\n"
        + "Implementation content. " * 100
        + "\n[INPUT NEEDED: KPI target]"
    )
    node = ProposalSubmissionGate(
        "submission_gate",
        {
            "proposal_text": proposal,
            "evidence_blockers": ["CL-7 has no verified passage"],
            "consistency_gate": "BLOCK",
            "consistency_findings": [
                {"message": "WP-3 has no lead partner"}
            ],
            "evaluation_threshold_passed": False,
            "evaluation_total_score": 9.5,
            "evaluation_blockers": [],
            "minimum_proposal_characters": 1000,
        },
    )

    result = await node.run({}, node.config.model_dump())

    assert result["status"] == "BLOCKED"
    assert result["submission_ready"] is False
    assert result["input_needed_count"] == 1
    assert any("Evidence:" in item for item in result["blockers"])
    assert any("consistency gate" in item for item in result["blockers"])
    assert any("evaluation did not pass" in item for item in result["blockers"])


@pytest.mark.asyncio
async def test_submission_gate_can_release_a_complete_proposal():
    proposal = (
        "# 1 Excellence\n"
        + "Grounded content. " * 200
        + "\n# 2 Impact\n"
        + "Impact content. " * 200
        + "\n# 3 Implementation\n"
        + "Implementation content. " * 200
    )
    node = ProposalSubmissionGate(
        "submission_gate",
        {
            "proposal_text": proposal,
            "evidence_blockers": [],
            "consistency_gate": "PASS",
            "consistency_findings": [],
            "evaluation_threshold_passed": True,
            "evaluation_total_score": 13.0,
            "evaluation_blockers": [],
            "minimum_proposal_characters": 1000,
        },
    )

    result = await node.run({}, node.config.model_dump())

    assert result["status"] == "READY"
    assert result["submission_ready"] is True
    assert result["blockers"] == []
