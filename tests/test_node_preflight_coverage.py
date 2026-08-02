"""Forces a human to review preflight coverage whenever a node type is added
or removed — see app/nodes/base.py's `required_services`/
`preflight_output_fields` extension points and app/runtime/preflight.py's
generic dispatch to them.

This is a snapshot/acknowledgment test, not a heuristic one: it can't be
satisfied by accident, only by deliberately updating ACKNOWLEDGED_NODE_TYPES
after checking whether the new type needs either override.
"""
from __future__ import annotations

import app.nodes  # noqa: F401 - populates the registry via discovery
from app.nodes.registry import NodeRegistry

ACKNOWLEDGED_NODE_TYPES: frozenset[str] = frozenset({
    "BoundedDeepResearchAgent",
    "CallCoverageMatrixAgent",
    "CitationRegistryBuilder",
    "ClaimEvidenceVerifier",
    "ConceptAlternativesAgent",
    "ConceptFreezeAgent",
    "ConsistencyChecker",
    "DOCXProposalRenderer",
    "DynamicFigureAgent",
    "Echo",
    "ExcelTableExtractor",
    "FigureEmbedder",
    "GraphNormalizer",
    "HorizonDOCXProposalRenderer",
    "HorizonEvaluationAgent",
    "HorizonHTMLProposalRenderer",
    "HumanInLoopAgent",
    "InternalProjectEvidenceRetrieverAgent",
    "KimiVisionAgent",
    "Literal",
    "MCPAgent",
    "MethodologyEngineeringAgent",
    "MinIOEvidenceIngestion",
    "OpenAIImageGenerationAgent",
    "PDFProposalRenderer",
    "PDFTextExtractor",
    "PaperQAEvidenceSynthesizerAgent",
    "PowerPointProposalSlides",
    "PriorProjectRetrieverAgent",
    "ProposalEvidenceFactoryAgent",
    "ProposalSubmissionGate",
    "ProposalTruthGraphAgent",
    "RAGAgent",
    "ResearchSourceAcquirer",
    "RouterAgent",
    "ScholarlyCandidateDiscoveryAgent",
    "ScientificResearchPlannerAgent",
    "ScientificSkillAgent",
    "StructuredDatasetRetrieverAgent",
    "TextAssemblerAgent",
    "TransformAgent",
    "WebSearchAgent",
    "WorkflowFileLoader",
})


def test_new_node_types_are_reviewed_for_preflight_coverage():
    current = set(NodeRegistry._registry)
    new_types = current - ACKNOWLEDGED_NODE_TYPES
    assert not new_types, (
        f"New node type(s) {sorted(new_types)} added without preflight review. "
        "Check whether they need a required_services() override (external "
        "service dependency, e.g. llm/object_store/web_search) or a "
        "preflight_output_fields() override (output fields beyond the "
        "static output_schema, e.g. a declared structured sub-schema) — see "
        "app/nodes/base.py. Once reviewed, add the type name to "
        "ACKNOWLEDGED_NODE_TYPES in this test."
    )
    removed = ACKNOWLEDGED_NODE_TYPES - current
    assert not removed, (
        f"Node type(s) {sorted(removed)} were removed from the registry; "
        "drop them from ACKNOWLEDGED_NODE_TYPES in this test."
    )
