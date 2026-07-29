from __future__ import annotations

from pathlib import Path

import pytest

from app.nodes.proposal_submission_gate import ProposalSubmissionGate
from app.runtime.loader import load_workflow
from app.runtime.preflight import preflight_workflow_yaml


AUTONOMOUS = Path(
    "workflows/horizon_proposal_autonomous_docx.yaml"
)
HITL = Path(
    "workflows/horizon_proposal_hitl_pdf.yaml"
)


def _types(path: Path) -> list[str]:
    return [node.type for node in load_workflow(path).nodes]


def test_autonomous_workflow_has_no_human_pause_and_only_docx_export():
    node_types = _types(AUTONOMOUS)

    assert len(node_types) == 26
    assert "HumanInLoopAgent" not in node_types
    assert node_types.count("HorizonDOCXProposalRenderer") == 1
    assert "HorizonHTMLProposalRenderer" not in node_types
    assert node_types[-1] == "HorizonDOCXProposalRenderer"


def test_human_reviewed_workflow_has_four_gates_and_only_pdf_export():
    node_types = _types(HITL)

    assert len(node_types) == 30
    assert node_types.count("HumanInLoopAgent") == 4
    assert node_types.count("HorizonHTMLProposalRenderer") == 1
    assert "HorizonDOCXProposalRenderer" not in node_types
    assert node_types[-1] == "HorizonHTMLProposalRenderer"


@pytest.mark.parametrize("path", [AUTONOMOUS, HITL])
def test_proposal_workflows_use_auto_for_generation_and_pass_preflight(
    path: Path,
):
    spec = load_workflow(path)
    llm_node_types = {
        "TransformAgent",
        "GraphNormalizer",
        "ScholarlyCandidateDiscoveryAgent",
        "ProposalEvidenceFactoryAgent",
        "ScientificSkillAgent",
        "ConceptAlternativesAgent",
    }
    auto_nodes = [
        node
        for node in spec.nodes
        if node.type in llm_node_types
    ]

    assert auto_nodes
    assert all(node.selected_model == "auto" for node in auto_nodes)
    assert all(
        node.model_routing is not None
        for node in auto_nodes
    )

    report = preflight_workflow_yaml(path.read_text(encoding="utf-8"))
    assert report.valid is True
    assert report.tokens_spent == 0
    assert report.errors == []
    assert report.warnings == []


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
