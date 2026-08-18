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
    # Core primitives. Each was reviewed against both extension points:
    #   AITaskAgent          — required_services {llm, cost_ledger};
    #                          preflight_output_fields exposes result.<path>
    #                          from its visual output schema.
    #   DecisionAgent        — no services (deterministic);
    #                          preflight_output_fields exposes decisions.<field>
    #                          for every field its rules and defaults can set.
    #   DataTransformAgent   — no services; preflight_output_fields exposes
    #                          data.<target> per configured operation.
    #   WorkflowInputAgent   — no services; preflight_output_fields exposes
    #                          data.<field> per declared field.
    #   EmailAgent           — required_services {email}; preflight_output_fields
    #                          exposes the message shape as message.<field> and
    #                          messages.items.<field>.
    "AITaskAgent",
    "DataTransformAgent",
    "DecisionAgent",
    #   MCPToolAgent         — required_services {mcp}; preflight_output_fields
    #                          declares `data.*`/`first.*` as prefixes because
    #                          the sub-shape comes from the MCP server's own
    #                          output schema, which preflight does not contact.
    "EmailAgent",
    "MCPToolAgent",
    "WorkflowInputAgent",
    #   StartAgent           — WorkflowInputAgent's successor; no services;
    #                          preflight_output_fields exposes data.<field> per
    #                          declared field (input_form mode) or is left at
    #                          the static message/attachments schema (chatbot
    #                          mode) — same pattern as WorkflowInputAgent.
    #   EndAgent             — no services; preflight_output_fields exposes
    #                          result.<key> per configured output
    #                          (workflow_result), result.title/result.message
    #                          (custom_response), or the fixed chat_response
    #                          keys actually configured (chat_response) —
    #                          never a static, fully-known shape since the
    #                          workflow author chooses the keys.
    "StartAgent",
    "EndAgent",
    #   ExternalActionAgent  — required_services {external_action}; no
    #                          preflight_output_fields override needed —
    #                          response_body is a static output_schema field,
    #                          not a dynamic sub-schema (unlike MCP's data.*).
    "ExternalActionAgent",
    #   SubprocessAgent      — required_services {audit_db,
    #                          background_run_manager}; preflight_output_fields
    #                          left at the default (result is a static
    #                          output_schema field) — its dynamic sub-shape is
    #                          exposed instead via the subprocess_output_paths
    #                          typed-output builder (logic_preflight.py),
    #                          enriching the field index without marking the
    #                          node closed-world (same principle as MCP's own
    #                          data.*/first.* handling).
    "SubprocessAgent",
    #   SQLQueryAgent        — required_services {mcp}; preflight_output_fields
    #                          overridden to the static envelope only —
    #                          rows/first hold whatever columns the author's
    #                          own SELECT names, never knowable statically
    #                          (unlike a classified MCP tool's fixed
    #                          output_schema), so they stay untyped rather
    #                          than guessed at.
    "SQLQueryAgent",
    #   PythonSnippetAgent   — required_services {python_runner};
    #                          preflight_static_output_values reports
    #                          result: {} when no output_fields are
    #                          declared (mirrors TransformAgent's own
    #                          no-schema case) — the dynamic sub-shape
    #                          (when output_fields IS declared) is exposed
    #                          via the python_snippet_paths typed-output
    #                          builder instead.
    "PythonSnippetAgent",

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
    #   IntegrationAgent     — required_services {files_integration,
    #                          object_store}; preflight_output_fields exposes
    #                          file.<field>/first.<field>/files.items.<field>
    #                          (the CloudFileMeta shape) and
    #                          downloaded_file.<field> (the CloudFileRef
    #                          shape) — mirrors EmailAgent's
    #                          message.<field>/messages.items.<field> pattern.
    "IntegrationAgent",
    "InternalProjectEvidenceRetrieverAgent",
    "KimiVisionAgent",
    #   KnowledgeRetrieval   — required_services {retrieval_service}; no
    #                          preflight_output_fields override needed — every
    #                          output field is statically declared on its
    #                          output_schema, none come from a dynamic sub-shape.
    "KnowledgeRetrieval",
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
