"""Generation-regression coverage for every registered node type.

Mirrors the ACKNOWLEDGED_NODE_TYPES snapshot pattern in
tests/test_node_preflight_coverage.py: a fixture map keyed by type_name that
must stay in lockstep with the live registry. Adding a node type without
adding it here fails this suite with a clear message, which is what keeps the
generator's coverage from silently drifting behind the node library.

No LLM is called here (no API key needed in CI). What's actually being
proven, per fixture, with zero tokens:

  1. the target type appears in a real, valid workflow — reused from the
     checked-in workflows/ tree wherever one already exercises the type, and
     authored as the smallest valid graph around it under
     workflows/test_fixtures/generation_coverage/ otherwise (see that
     directory for the ~8 types nothing else on disk used yet);
  2. that workflow passes the same deterministic preflight the generation
     endpoint gates on (app.runtime.preflight.preflight_workflow_yaml) — valid
     node types, config, edges, entry/exit, template references;
  3. a realistic natural-language request for that capability is actually
     shortlisted to include the type by the deterministic capability
     selector (app.workflow.capability_selection.select_candidate_node_types)
     — proving the staged generation pipeline would even get a chance to use
     it, without spending a token to check.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import app.nodes  # noqa: F401 - populates the registry via discovery
import yaml

from app.nodes.registry import NodeRegistry
from app.runtime.preflight import preflight_workflow_yaml
from app.workflow.capability_selection import select_candidate_node_types

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = NodeRegistry.manifest()


@dataclass(frozen=True)
class GenerationFixture:
    # A natural-language ask that should lead the generator to this node type.
    request: str
    # Path (relative to the repo root) to a real, valid workflow that uses it.
    workflow_path: str


# One entry per registered node type. Reuses an existing workflow wherever one
# already exercises the type; only ~8 types needed a new minimal fixture (see
# workflows/test_fixtures/generation_coverage/) — check there before adding a
# whole new file for a type that already appears on disk somewhere.
NODE_TYPE_GENERATION_FIXTURES: dict[str, GenerationFixture] = {
    "AITaskAgent": GenerationFixture(
        "Extract structured information from a customer email and classify its intent.",
        # AITaskAgent is deprecated in favor of TransformAgent's new-style
        # editor (see app/nodes/ai_task.py) — its 3 real production instances
        # were migrated, so no checked-in workflow uses it anymore. This
        # minimal fixture keeps coverage complete while the type stays
        # registered.
        "workflows/test_fixtures/generation_coverage/ai_task_agent.yaml",
    ),
    "BoundedDeepResearchAgent": GenerationFixture(
        "Run a bounded deep research job with a web-search tool-calling loop to build research dossiers.",
        "workflows/concept_note_to_10-page_methodology_section1.yaml",
    ),
    "CallCoverageMatrixAgent": GenerationFixture(
        "Build a deterministic requirement-by-requirement call coverage matrix for the submission gate.",
        "workflows/horizon_partb_evidence.yaml",
    ),
    "CitationRegistryBuilder": GenerationFixture(
        "Build a numbered citation registry from acquired full-text documents for the renderer.",
        "workflows/horizon_v4.yaml",
    ),
    "ClaimEvidenceVerifier": GenerationFixture(
        "Verify each linked claim against an exact passage in an immutable source version.",
        "workflows/test_fixtures/generation_coverage/claim_evidence_verifier.yaml",
    ),
    "ConceptAlternativesAgent": GenerationFixture(
        "Generate conservative, balanced, and ambitious Horizon concepts from the approved proposal graph.",
        "workflows/horizon_partb_evidence.yaml",
    ),
    "ConceptFreezeAgent": GenerationFixture(
        "Resolve the human gate's concept decision against the generated alternatives.",
        "workflows/horizon_partb_evidence.yaml",
    ),
    "ConsistencyChecker": GenerationFixture(
        "Run a consistency checker over the proposal draft before submission.",
        "workflows/test_fixtures/agro_thrive_partb.yaml",
    ),
    "DOCXProposalRenderer": GenerationFixture(
        "Render the proposal sections into a styled corporate docx document.",
        "workflows/test_fixtures/generation_coverage/docx_proposal_renderer.yaml",
    ),
    "DataTransformAgent": GenerationFixture(
        "Reshape the extracted data by renaming and merging fields before sending it onward.",
        "workflows/multilingual_customer_request_triage.yaml",
    ),
    "DecisionAgent": GenerationFixture(
        "Apply business rules to decide whether this case needs escalation.",
        "workflows/multilingual_customer_request_triage.yaml",
    ),
    "DynamicFigureAgent": GenerationFixture(
        "Generate a diagram image for every image prompt marker and embed it in the document.",
        "workflows/concept_note_to_10-page_methodology_section1.yaml",
    ),
    "Echo": GenerationFixture(
        "Render a template string with the drafted values.",
        "workflows/test_fixtures/hello_workflow.yaml",
    ),
    "EmailAgent": GenerationFixture(
        "Search the shared inbox for unread messages and reply to the customer.",
        "workflows/test_fixtures/generation_coverage/email_agent.yaml",
    ),
    "ExternalActionAgent": GenerationFixture(
        "Call an external logistics API to check a shipment's delivery status.",
        "workflows/test_fixtures/generation_coverage/external_action_agent.yaml",
    ),
    "ExcelTableExtractor": GenerationFixture(
        "Extract tables from an uploaded xlsx spreadsheet in storage.",
        "workflows/test_fixtures/generation_coverage/excel_table_extractor.yaml",
    ),
    "FigureEmbedder": GenerationFixture(
        "Replace image prompt placeholders with real embedded images before rendering the proposal.",
        "workflows/horizon_partb_drafts_to_docx.yaml",
    ),
    "GraphNormalizer": GenerationFixture(
        "Run the graph normalizer to clean up and validate the evidence graph structure.",
        "workflows/database_lookup_smoke_test.yaml",
    ),
    "HorizonDOCXProposalRenderer": GenerationFixture(
        "Convert the Horizon Europe Part B proposal markdown into an editable citation-aware docx with a table of contents.",
        "workflows/concept_note_to_10-page_methodology_section1.yaml",
    ),
    "HorizonEvaluationAgent": GenerationFixture(
        "Score Excellence, Impact, and Implementation with independent cross-provider evaluators.",
        "workflows/horizon_partb_drafts_to_docx.yaml",
    ),
    "HorizonHTMLProposalRenderer": GenerationFixture(
        "Convert the Horizon Europe Part B proposal into a citation-aware pdf with cover and table of contents.",
        "workflows/call_documents_to_pdf1.yaml",
    ),
    "HumanInLoopAgent": GenerationFixture(
        "Pause the workflow and wait for a human to approve the drafted response.",
        "workflows/hitl_editor_demo.yaml",
    ),
    "InternalProjectEvidenceRetrieverAgent": GenerationFixture(
        "Retrieve internal partner, pilot, and budget facts requiring an exact source passage and human approval.",
        "workflows/internal_evidence_lookup_smoke_test.yaml",
    ),
    "KimiVisionAgent": GenerationFixture(
        "Analyse an uploaded image with Kimi K3 vision.",
        "workflows/kimi_vision_agent_demo.yaml",
    ),
    "KnowledgeRetrieval": GenerationFixture(
        "Retrieve secured knowledge through a saved retrieval profile without generating an answer.",
        "workflows/test_fixtures/generation_coverage/knowledge_retrieval.yaml",
    ),
    "Literal": GenerationFixture(
        "Emit a literal configured value as the workflow's starting output.",
        "workflows/test_fixtures/hello_workflow.yaml",
    ),
    "MCPAgent": GenerationFixture(
        "Run an LLM-driven agent loop that uses MCP tools to accomplish the objective.",
        "workflows/test_fixtures/generation_coverage/mcp_agent.yaml",
    ),
    "MCPToolAgent": GenerationFixture(
        "Call a tool on our connected CRM system to look up the account.",
        "workflows/crm_aware_customer_triage.yaml",
    ),
    "MethodologyEngineeringAgent": GenerationFixture(
        "Produce a skill-guided Method Card per frozen concept objective, with baseline and validation.",
        "workflows/horizon_partb_evidence.yaml",
    ),
    "MinIOEvidenceIngestion": GenerationFixture(
        "Index acquired full-text sources from MinIO pages into Weaviate with citation display numbers.",
        "workflows/horizon_v4.yaml",
    ),
    "OpenAIImageGenerationAgent": GenerationFixture(
        "Generate an image with an approved OpenAI image model and store it in object storage.",
        "workflows/horizon_partb_drafts.yaml",
    ),
    "PDFProposalRenderer": GenerationFixture(
        "Render the proposal sections to a styled corporate pdf document.",
        "workflows/test_fixtures/proposal_generation.yaml",
    ),
    "PDFTextExtractor": GenerationFixture(
        "Extract text from an uploaded pdf stored in object storage.",
        "workflows/test_fixtures/generation_coverage/pdf_text_extractor.yaml",
    ),
    "PaperQAEvidenceSynthesizerAgent": GenerationFixture(
        "Run PaperQA2 over already-acquired full-text documents for gap-aware literature synthesis.",
        "workflows/literature_review_synthesis.yaml",
    ),
    "PowerPointProposalSlides": GenerationFixture(
        "Build a PowerPoint deck from the proposal sections.",
        "workflows/test_fixtures/generation_coverage/powerpoint_proposal_slides.yaml",
    ),
    "PriorProjectRetrieverAgent": GenerationFixture(
        "Search official CORDIS, LIFE and EIP-AGRI project records for precedents and synergies.",
        "workflows/research_lookup_smoke_test.yaml",
    ),
    "ProposalEvidenceFactoryAgent": GenerationFixture(
        "Verify proposal claims against immutable full-text pages and build an auditable citation registry.",
        "workflows/test_fixtures/agro_thrive_partb.yaml",
    ),
    "ProposalSubmissionGate": GenerationFixture(
        "Deterministically decide whether the proposal is ready for final human approval or export.",
        "workflows/horizon_partb_drafts_to_docx.yaml",
    ),
    "ProposalTruthGraphAgent": GenerationFixture(
        "Freeze a drafting-safe truth graph containing only verified evidence links and explicit gaps.",
        "workflows/horizon_partb_evidence.yaml",
    ),
    "RAGAgent": GenerationFixture(
        "Answer the question with hybrid retrieval and grounded citations.",
        "workflows/test_fixtures/proposal_generation.yaml",
    ),
    "ResearchSourceAcquirer": GenerationFixture(
        "Resolve and store bounded deep research citations as immutable source versions.",
        "workflows/abm_playbook.yaml",
    ),
    "RouterAgent": GenerationFixture(
        "Branch the workflow based on the customer's category into the right team.",
        "workflows/lead_enrichment_qualification.yaml",
    ),
    "ScholarlyCandidateDiscoveryAgent": GenerationFixture(
        "Find scholarly candidate records with multi-query and contradiction searches.",
        "workflows/literature_review_synthesis.yaml",
    ),
    "ScientificResearchPlannerAgent": GenerationFixture(
        "Turn the call and selected concept into several bounded research briefs routed through approved skills.",
        "workflows/concept_note_to_10-page_methodology_section1.yaml",
    ),
    "ScientificSkillAgent": GenerationFixture(
        "Perform scientific synthesis guided by an approved Agent Skill.",
        "workflows/horizon_partb_drafts_to_docx.yaml",
    ),
    "StructuredDatasetRetrieverAgent": GenerationFixture(
        "Retrieve bounded Eurostat structured data with explicit filters and auditable provenance.",
        "workflows/database_lookup_smoke_test.yaml",
    ),
    "SubprocessAgent": GenerationFixture(
        "Run another saved workflow as a reusable business subprocess and wait for it to finish.",
        "workflows/test_fixtures/generation_coverage/subprocess_agent.yaml",
    ),
    "SQLQueryAgent": GenerationFixture(
        "Run a read-only SQL query against the business-records database to look up matching accounts.",
        "workflows/test_fixtures/generation_coverage/sql_query_agent.yaml",
    ),
    "PythonSnippetAgent": GenerationFixture(
        "Run a short Python snippet to add two numbers together in an isolated sandbox.",
        "workflows/test_fixtures/generation_coverage/python_snippet_agent.yaml",
    ),
    "TextAssemblerAgent": GenerationFixture(
        "Deterministically join pre-rendered text parts with a separator to assemble the final document.",
        "workflows/concept_note_to_10-page_methodology_section1.yaml",
    ),
    "TransformAgent": GenerationFixture(
        "Summarize and rewrite the extracted text with a pure LLM transform.",
        "workflows/local_llm_smoke.yaml",
    ),
    "WebSearchAgent": GenerationFixture(
        "Search the live public web for a competitor's recent pricing changes.",
        "workflows/personalized_outbound_campaigning.yaml",
    ),
    "WorkflowFileLoader": GenerationFixture(
        "Load an uploaded workflow file and extract its text content.",
        "workflows/test_fixtures/file_input_demo.yaml",
    ),
    "WorkflowInputAgent": GenerationFixture(
        "Declare the information entering the workflow from the incoming API request.",
        "workflows/multilingual_customer_request_triage.yaml",
    ),
    "StartAgent": GenerationFixture(
        "Start a workflow with a business-friendly input form or a chatbot message.",
        "workflows/test_fixtures/generation_coverage/start_end_agent.yaml",
    ),
    "EndAgent": GenerationFixture(
        "Return the workflow's final answer as its result.",
        "workflows/test_fixtures/generation_coverage/start_end_agent.yaml",
    ),
}


def _fixture_yaml(fixture: GenerationFixture) -> str:
    return (_REPO_ROOT / fixture.workflow_path).read_text()


def _node_types_in(yaml_text: str) -> set[str]:
    doc = yaml.safe_load(yaml_text)
    return {
        node["type"]
        for node in (doc.get("nodes") or [])
        if isinstance(node, dict) and node.get("type")
    }


def test_every_registered_node_type_has_generation_coverage():
    """The CI tripwire: a new node type with no fixture here fails loudly,
    and a fixture for a type that was removed fails just as loudly — same
    symmetric contract as test_node_preflight_coverage.py."""
    registered = set(NodeRegistry._registry)
    covered = set(NODE_TYPE_GENERATION_FIXTURES)

    missing = registered - covered
    stale = covered - registered

    report_lines = [
        "",
        "Workflow generation coverage report:",
        f"  Registered node types:  {len(registered)}",
        f"  Node types with a fixture: {len(covered)}",
        f"  Missing coverage:       {sorted(missing) or 'none'}",
    ]
    print("\n".join(report_lines))

    assert not missing, (
        f"New node type(s) {sorted(missing)} were registered without workflow-"
        "generation coverage. Add an entry to NODE_TYPE_GENERATION_FIXTURES in "
        "this file — reuse an existing workflows/*.yaml that already contains "
        "the type if one exists, or author the smallest valid workflow around "
        "it under workflows/test_fixtures/generation_coverage/ otherwise."
    )
    assert not stale, (
        f"Fixture(s) for {sorted(stale)} reference node type(s) no longer in "
        "the registry — remove their entries from NODE_TYPE_GENERATION_FIXTURES."
    )


def _check_fixture(type_name: str, fixture: GenerationFixture) -> list[str]:
    problems = []
    yaml_text = _fixture_yaml(fixture)

    if type_name not in _node_types_in(yaml_text):
        problems.append(f"{fixture.workflow_path} does not actually use {type_name}")

    report = preflight_workflow_yaml(yaml_text)
    if not report.valid:
        issues = "; ".join(f"{i.code}: {i.message}" for i in report.errors)
        problems.append(f"{fixture.workflow_path} fails preflight: {issues}")

    shortlist = select_candidate_node_types(fixture.request, _MANIFEST)
    if type_name not in shortlist:
        problems.append(
            f"request {fixture.request!r} did not surface {type_name} "
            f"(shortlist: {shortlist})"
        )

    return problems


def test_every_fixture_uses_its_type_passes_preflight_and_is_capability_matched():
    """One assertion per fixture rather than a bare loop-with-assert, so a
    failure names every problem across all ~50 types in one run instead of
    stopping at the first."""
    failures: dict[str, list[str]] = {}
    for type_name, fixture in NODE_TYPE_GENERATION_FIXTURES.items():
        problems = _check_fixture(type_name, fixture)
        if problems:
            failures[type_name] = problems

    passing = len(NODE_TYPE_GENERATION_FIXTURES) - len(failures)
    print(
        "\nGeneration fixture results: "
        f"{passing}/{len(NODE_TYPE_GENERATION_FIXTURES)} passing, "
        f"{len(failures)} failing."
    )

    assert not failures, "\n".join(
        f"{type_name}: {'; '.join(problems)}" for type_name, problems in failures.items()
    )
