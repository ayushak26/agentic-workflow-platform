from __future__ import annotations

from pathlib import Path

import pytest

from app.nodes.proposal_submission_gate import ProposalSubmissionGate
from app.runtime.loader import load_workflow
from app.runtime.preflight import preflight_workflow_yaml


# horizon_proposal_autonomous_docx.yaml was split/renamed to
# horizon_partb_autonomous_docx.yaml when the Part B pipeline was staged
# (see workflows/horizon_partb_{evidence,drafts,drafts_to_docx}.yaml and
# workflows/pipelines/horizon_partb.pipeline.yaml for the staged version).
AUTONOMOUS = Path(
    "workflows/horizon_partb_autonomous_docx.yaml"
)
HITL = Path(
    "workflows/horizon_proposal_hitl_pdf.yaml"
)


def _types(path: Path) -> list[str]:
    return [node.type for node in load_workflow(path).nodes]


def test_autonomous_workflow_has_no_human_pause_and_only_docx_export():
    node_types = _types(AUTONOMOUS)

    # 32 original nodes + 7 added when compile/revise were split per
    # criterion (Excellence/Impact/Implementation) to avoid a single
    # TransformAgent call having to hold a ~40-44 page document within its
    # max_tokens output: compile_excellence, compile_impact,
    # compile_implementation, revise_excellence, revise_impact,
    # revise_implementation, plus research_documentation (citation/web-search
    # audit trail). compile_v1 and final_revision keep their ids but are now
    # TextAssemblerAgent (deterministic, non-LLM join) nodes instead of
    # TransformAgent. -1 for the removed scientific_synthesis node
    # (ScientificSkillAgent); downstream drafts now source verified-evidence
    # narrative directly from verify_evidence.proposal_ready_cited_markdown.
    # +1 for scientific_peer_review (ScientificSkillAgent), reinstated as a
    # dedicated node once the bounded Deep Research / truth-graph machinery
    # (ScientificResearchPlannerAgent, BoundedDeepResearchAgent,
    # ResearchSourceAcquirer, ProposalTruthGraphAgent) replaced
    # scientific_synthesis's old evidence path.
    assert len(node_types) == 39
    assert "HumanInLoopAgent" not in node_types
    assert node_types.count("HorizonDOCXProposalRenderer") == 1
    assert "HorizonHTMLProposalRenderer" not in node_types
    assert node_types.count("TextAssemblerAgent") == 2
    assert node_types[-1] == "HorizonDOCXProposalRenderer"


def test_human_reviewed_workflow_has_four_gates_and_only_pdf_export():
    node_types = _types(HITL)

    # 37 original nodes + 7 added by the same compile/revise split described
    # above (see test_autonomous_workflow_has_no_human_pause_and_only_docx_export),
    # -1 for the removed scientific_synthesis node (same reason as above).
    assert len(node_types) == 43
    assert node_types.count("HumanInLoopAgent") == 4
    assert node_types.count("HorizonHTMLProposalRenderer") == 1
    assert "HorizonDOCXProposalRenderer" not in node_types
    assert node_types.count("TextAssemblerAgent") == 2
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
        # Cheap, near-deterministic extraction/composition steps (renderer
        # metadata, a one-line search query) are deliberately pinned to a
        # fast/cheap model rather than routed for accuracy — the same
        # accuracy_priority: "economy" signal the model router itself uses
        # to deprioritize cost. "Use auto for generation" means the
        # generation-heavy steps, not every single TransformAgent.
        and not (
            node.model_routing is not None
            and node.model_routing.accuracy_priority == "economy"
        )
    ]

    assert auto_nodes
    # horizon_partb_autonomous_docx.yaml deliberately pins the model per task
    # to the scientific_deep_research_pipeline.md routing table (gpt-5.6-sol
    # for final drafting/revision/blueprint, gpt-5.6-terra for planning/call
    # synthesis/compilation, o3 for evidence verification/peer review/red
    # team) instead of letting the generic router decide via "auto".
    pinned_routing_table_models = {"gpt-5.6-sol", "gpt-5.6-terra", "o3"}
    if path == AUTONOMOUS:
        assert all(
            node.selected_model == "auto"
            or node.selected_model in pinned_routing_table_models
            for node in auto_nodes
        )
    else:
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
