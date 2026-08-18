"""Category taxonomy for the node palette (Builder) and Cockpit's node
inspector. A single lookup table rather than a field on every NodeType
subclass — simpler to keep current than touching 40+ node files whenever the
grouping changes.
"""
from __future__ import annotations

NODE_CATEGORIES: dict[str, str] = {
    # Core Building Blocks — the small reusable vocabulary a new business
    # workflow is normally expressed in. Every one of these is behaviour-by-
    # configuration: the prompt, schema, labels, thresholds, rules, routes and
    # provider are all authored in the Builder, so a new business process needs
    # no new node type.
    "StartAgent": "Core Building Blocks",
    "EndAgent": "Core Building Blocks",
    "WorkflowInputAgent": "Core Building Blocks",
    "AITaskAgent": "Core Building Blocks",
    "DecisionAgent": "Core Building Blocks",
    "RouterAgent": "Core Building Blocks",
    "DataTransformAgent": "Core Building Blocks",
    "HumanInLoopAgent": "Core Building Blocks",
    "EmailAgent": "Core Building Blocks",
    "IntegrationAgent": "Core Building Blocks",
    "MCPToolAgent": "Core Building Blocks",
    "ExternalActionAgent": "Core Building Blocks",

    # Control & Flow — routing, transforms, plain I/O
    "Literal": "Control & Flow",
    "Echo": "Control & Flow",
    "TransformAgent": "Control & Flow",
    "WorkflowFileLoader": "Control & Flow",
    "TextAssemblerAgent": "Control & Flow",
    "SubprocessAgent": "Control & Flow",

    # Research & Discovery — finding candidate sources and papers
    "BoundedDeepResearchAgent": "Research & Discovery",
    "WebSearchAgent": "Research & Discovery",
    "ScholarlyCandidateDiscoveryAgent": "Research & Discovery",
    "ResearchSourceAcquirer": "Research & Discovery",
    "ScientificResearchPlannerAgent": "Research & Discovery",

    # Evidence & Retrieval — pulling and verifying evidence for claims
    "RAGAgent": "Evidence & Retrieval",
    "KnowledgeRetrieval": "Evidence & Retrieval",
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
    "SQLQueryAgent": "Integrations",
    "PythonSnippetAgent": "Integrations",
}

# Any type_name not listed above (including future node types) falls back to
# this catch-all so the UI never breaks on an unmapped type.
DEFAULT_CATEGORY = "Other"


def category_for(type_name: str) -> str:
    return NODE_CATEGORIES.get(type_name, DEFAULT_CATEGORY)


# Presentation-only: one icon per category (not per node type) for the
# Builder's node palette. Names must exist in ui/src/components/ui/Icon.tsx.
CATEGORY_ICONS: dict[str, str] = {
    "Core Building Blocks": "topology",
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


# --------------------------------------------------------------------------
# Family and execution kind
# --------------------------------------------------------------------------
# New core primitives declare `family` / `execution_kind` / `about` on the class
# itself (see app/nodes/base.py). The 43 pre-existing node types predate those
# ClassVars, so their classification lives here rather than in 43 edits — the
# same reason the category table exists. The registry manifest prefers a class's
# own declaration and falls back to these tables.

CORE_NODE_TYPES: frozenset[str] = frozenset(
    {
        "WorkflowInputAgent",
        "AITaskAgent",
        "DecisionAgent",
        "RouterAgent",
        "DataTransformAgent",
        "HumanInLoopAgent",
        "EmailAgent",
        "MCPToolAgent",
    }
)

# Execution kind for pre-existing types. Anything unlisted is inferred from
# whether the node needs the `llm` service (see registry.manifest), which is
# accurate for the remaining cases and cannot go stale.
_EXECUTION_KINDS: dict[str, str] = {
    "HumanInLoopAgent": "human",
    "MCPAgent": "external",
    "MCPToolAgent": "external",
    "ScientificSkillAgent": "external",
    "WebSearchAgent": "external",
    "MinIOEvidenceIngestion": "external",
    "ResearchSourceAcquirer": "external",
    "ScholarlyCandidateDiscoveryAgent": "external",
    "OpenAIImageGenerationAgent": "ai",
    "KimiVisionAgent": "ai",
    "DOCXProposalRenderer": "output",
    "HorizonDOCXProposalRenderer": "output",
    "HorizonHTMLProposalRenderer": "output",
    "PDFProposalRenderer": "output",
    "PowerPointProposalSlides": "output",
    "PDFTextExtractor": "input",
    "ExcelTableExtractor": "input",
    "WorkflowFileLoader": "input",
    "Literal": "input",
    "Echo": "deterministic",
    "TransformAgent": "ai",
    "TextAssemblerAgent": "deterministic",
    "GraphNormalizer": "deterministic",
    "FigureEmbedder": "deterministic",
    "CitationRegistryBuilder": "deterministic",
    "CallCoverageMatrixAgent": "deterministic",
    "ProposalSubmissionGate": "deterministic",
}


def family_for(type_name: str) -> str:
    return "core" if type_name in CORE_NODE_TYPES else "specialized"


def execution_kind_for(type_name: str, *, uses_llm: bool) -> str:
    declared = _EXECUTION_KINDS.get(type_name)
    if declared:
        return declared
    return "ai" if uses_llm else "deterministic"
