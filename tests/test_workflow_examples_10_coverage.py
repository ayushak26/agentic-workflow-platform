"""Node-coverage regression test for the 10-business-workflow portfolio
(docs/workflow_examples_10/). Mirrors the symmetric-contract pattern in
tests/test_workflow_generation_node_coverage.py, scoped to this specific
portfolio rather than the whole registry.

The portfolio deliberately does not cover every registered node type —
docs/workflow_examples_10/NODE_COVERAGE_MATRIX.md documents a specific
business reason for every exclusion. This test's job is to catch drift in
either direction: a node quietly added to the portfolio without updating the
documented set (which would mean the coverage numbers in README.md and
VALIDATION_REPORT.md are stale), or a node removed from a workflow that the
docs still claim is covered.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import app.nodes  # noqa: F401 - populates the registry via discovery
from app.nodes.registry import NodeRegistry

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS_DIR = _REPO_ROOT / "workflows"

# The 10 main business workflows.
PORTFOLIO_WORKFLOWS = [
    "w01_intelligent_customer_inquiry_resolution.yaml",
    "w02_new_customer_rfq_intake.yaml",
    "w03_technical_service_case.yaml",
    "w04_order_status_delivery_exceptions.yaml",
    "w05_quote_discount_approval.yaml",
    "w06_purchase_request_supplier_approval.yaml",
    "w07_invoice_exception_verification.yaml",
    "w08_employee_onboarding_orchestration.yaml",
    "w09_it_helpdesk_access_request.yaml",
    "w10_evidence_grounded_proposal.yaml",
]

# The reusable and workflow-specific subprocesses the 10 workflows call.
PORTFOLIO_SUBPROCESSES = [
    "sp01_multilingual_message_understanding.yaml",
    "sp02_customer_identity_resolution.yaml",
    "sp03_internal_knowledge_answer.yaml",
    "sp04_approval_gate.yaml",
    "sp05_response_preparation.yaml",
    "w01sub_route_and_notify.yaml",
    "w08sub_hr_setup.yaml",
    "w08sub_it_account.yaml",
]

PORTFOLIO_FILES = PORTFOLIO_WORKFLOWS + PORTFOLIO_SUBPROCESSES

# Every node type registered but hidden from the Builder's palette — never
# eligible for coverage in a hand-authored demo portfolio, since a new
# workflow can't drag one in. Kept in lockstep with
# ui/src/modes/studio/NodePalette.tsx's HIDDEN_FROM_PALETTE set.
HIDDEN_FROM_PALETTE = {"WorkflowInputAgent", "AITaskAgent", "DataTransformAgent"}

# Every demo-eligible node type this portfolio does NOT use, with the reason
# documented in NODE_COVERAGE_MATRIX.md's exclusion list. If a node is added
# to the portfolio, remove it from this set (the test enforces that the two
# never drift apart in either direction). If a genuinely new node type is
# registered upstream, it lands in `missing` below until someone decides
# whether to add it to the portfolio or to this documented-exclusion set.
DOCUMENTED_EXCLUSIONS = {
    "IntegrationAgent",  # demonstrated instead in workflows/test_fixtures/google_drive_rag_lookup.yaml
    "ExternalActionAgent",
    "Literal",
    "InternalProjectEvidenceRetrieverAgent",
    "PriorProjectRetrieverAgent",
    "StructuredDatasetRetrieverAgent",
    "PaperQAEvidenceSynthesizerAgent",
    "MinIOEvidenceIngestion",
    "ClaimEvidenceVerifier",
    "CitationRegistryBuilder",
    "KimiVisionAgent",
    "OpenAIImageGenerationAgent",
    "DynamicFigureAgent",
    "FigureEmbedder",
    "ExcelTableExtractor",
    "PDFTextExtractor",
    "PDFProposalRenderer",
    "DOCXProposalRenderer",
    "ScientificSkillAgent",
    "SQLQueryAgent",
}


def _node_types_used(yaml_text: str) -> set[str]:
    doc = yaml.safe_load(yaml_text)
    return {
        node["type"]
        for node in (doc.get("nodes") or [])
        if isinstance(node, dict) and node.get("type")
    }


def _all_portfolio_node_types() -> set[str]:
    used: set[str] = set()
    for filename in PORTFOLIO_FILES:
        path = _WORKFLOWS_DIR / filename
        used |= _node_types_used(path.read_text())
    return used


def test_every_portfolio_file_exists_and_parses():
    for filename in PORTFOLIO_FILES:
        path = _WORKFLOWS_DIR / filename
        assert path.is_file(), f"missing portfolio file: {filename}"
        doc = yaml.safe_load(path.read_text())
        assert isinstance(doc, dict) and doc.get("nodes"), (
            f"{filename} did not parse to a workflow with nodes"
        )


def test_portfolio_coverage_matches_documented_exclusions():
    """The symmetric contract: every demo-eligible node the portfolio
    doesn't use must be a *documented* exclusion, and every documented
    exclusion must genuinely be unused — so the two can never silently
    drift apart."""
    registered = set(NodeRegistry._registry)
    demo_eligible = registered - HIDDEN_FROM_PALETTE
    used = _all_portfolio_node_types()

    uncovered = demo_eligible - used
    undocumented_gaps = uncovered - DOCUMENTED_EXCLUSIONS
    stale_exclusions = DOCUMENTED_EXCLUSIONS - uncovered

    report_lines = [
        "",
        "10-workflow portfolio coverage report:",
        f"  Registered node types:      {len(registered)}",
        f"  Demo-eligible (not hidden): {len(demo_eligible)}",
        f"  Used by the portfolio:      {len(used & demo_eligible)}",
        f"  Documented exclusions:      {len(DOCUMENTED_EXCLUSIONS)}",
        f"  Undocumented gaps:          {sorted(undocumented_gaps) or 'none'}",
        f"  Stale exclusions:           {sorted(stale_exclusions) or 'none'}",
    ]
    print("\n".join(report_lines))

    assert not undocumented_gaps, (
        f"Node type(s) {sorted(undocumented_gaps)} are demo-eligible, unused by "
        "the portfolio, and not in DOCUMENTED_EXCLUSIONS. Either use one in a "
        "workflow, or add it to DOCUMENTED_EXCLUSIONS here and to "
        "docs/workflow_examples_10/NODE_COVERAGE_MATRIX.md's exclusion list "
        "with a real business reason."
    )
    assert not stale_exclusions, (
        f"DOCUMENTED_EXCLUSIONS claims {sorted(stale_exclusions)} are unused, "
        "but the portfolio actually uses them now — remove them from "
        "DOCUMENTED_EXCLUSIONS here and from NODE_COVERAGE_MATRIX.md's "
        "exclusion list."
    )


def test_hidden_from_palette_nodes_are_never_used_in_the_portfolio():
    """A hand-authored demo portfolio should model what an author can
    actually drag into the Builder — never a hidden, deprecated predecessor."""
    used = _all_portfolio_node_types()
    used_but_hidden = used & HIDDEN_FROM_PALETTE
    assert not used_but_hidden, (
        f"Portfolio uses hidden/deprecated node type(s) {sorted(used_but_hidden)} "
        "— replace with their current successor (TransformAgent/StartAgent)."
    )
