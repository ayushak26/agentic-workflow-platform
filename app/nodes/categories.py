"""Category taxonomy for the node palette (Builder) and Cockpit's node
inspector. A single lookup table rather than a field on every NodeType
subclass — simpler to keep current than touching 40+ node files whenever the
grouping changes.
"""
from __future__ import annotations

NODE_CATEGORIES: dict[str, str] = {
    # Control & Flow — routing, transforms, plain I/O, human review
    "Literal": "Control & Flow",
    "Echo": "Control & Flow",
    "RouterAgent": "Control & Flow",
    "TransformAgent": "Control & Flow",
    "WorkflowFileLoader": "Control & Flow",
    "TextAssemblerAgent": "Control & Flow",
    "HumanInLoopAgent": "Control & Flow",

    # Research & Discovery — finding candidate sources and papers
    "BoundedDeepResearchAgent": "Research & Discovery",
    "WebSearchAgent": "Research & Discovery",
    "ScholarlyCandidateDiscoveryAgent": "Research & Discovery",
    "ResearchSourceAcquirer": "Research & Discovery",
    "ScientificResearchPlannerAgent": "Research & Discovery",

    # Evidence & Retrieval — pulling and verifying evidence for claims
    "RAGAgent": "Evidence & Retrieval",
    "InternalProjectEvidenceRetrieverAgent": "Evidence & Retrieval",
    "PriorProjectRetrieverAgent": "Evidence & Retrieval",
    "StructuredDatasetRetrieverAgent": "Evidence & Retrieval",
    "PaperQAEvidenceSynthesizerAgent": "Evidence & Retrieval",
    "MinIOEvidenceIngestion": "Evidence & Retrieval",
    "ClaimEvidenceVerifier": "Evidence & Retrieval",
    "ProposalTruthGraphAgent": "Evidence & Retrieval",
    "CitationRegistryBuilder": "Evidence & Retrieval",

    # Proposal Engineering — the EU-proposal-specific reasoning/drafting agents
    "ConceptAlternativesAgent": "Proposal Engineering",
    "ConceptFreezeAgent": "Proposal Engineering",
    "MethodologyEngineeringAgent": "Proposal Engineering",
    "ProposalEvidenceFactoryAgent": "Proposal Engineering",
    "ProposalSubmissionGate": "Proposal Engineering",
    "ConsistencyChecker": "Proposal Engineering",
    "CallCoverageMatrixAgent": "Proposal Engineering",
    "GraphNormalizer": "Proposal Engineering",
    "HorizonEvaluationAgent": "Proposal Engineering",

    # Multimodal — vision and image generation
    "KimiVisionAgent": "Multimodal",
    "OpenAIImageGenerationAgent": "Multimodal",
    "DynamicFigureAgent": "Multimodal",
    "FigureEmbedder": "Multimodal",

    # Document Rendering & Export — reading and producing office documents
    "ExcelTableExtractor": "Document Rendering & Export",
    "PDFTextExtractor": "Document Rendering & Export",
    "PDFProposalRenderer": "Document Rendering & Export",
    "PowerPointProposalSlides": "Document Rendering & Export",
    "DOCXProposalRenderer": "Document Rendering & Export",
    "HorizonDOCXProposalRenderer": "Document Rendering & Export",
    "HorizonHTMLProposalRenderer": "Document Rendering & Export",

    # Integrations — external tool/skill protocols
    "MCPAgent": "Integrations",
    "ScientificSkillAgent": "Integrations",
}

# Any type_name not listed above (including future node types) falls back to
# this catch-all so the UI never breaks on an unmapped type.
DEFAULT_CATEGORY = "Other"


def category_for(type_name: str) -> str:
    return NODE_CATEGORIES.get(type_name, DEFAULT_CATEGORY)


# Presentation-only: one icon per category (not per node type) for the
# Builder's node palette. Names must exist in ui/src/components/ui/Icon.tsx.
CATEGORY_ICONS: dict[str, str] = {
    "Control & Flow": "topology",
    "Research & Discovery": "flask",
    "Evidence & Retrieval": "checklist",
    "Proposal Engineering": "terminal",
    "Multimodal": "cloud",
    "Document Rendering & Export": "layout",
    "Integrations": "settings",
    DEFAULT_CATEGORY: "menu",
}

DEFAULT_ICON = "topology"


def icon_for(type_name: str) -> str:
    return CATEGORY_ICONS.get(category_for(type_name), DEFAULT_ICON)
