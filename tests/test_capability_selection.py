from __future__ import annotations

import app.nodes  # noqa: F401 - populates the registry via discovery
from app.nodes.registry import NodeRegistry
from app.workflow.capability_selection import select_candidate_node_types

MANIFEST = NodeRegistry.manifest()
CORE_TYPES = {e["type_name"] for e in MANIFEST if e["family"] == "core"}


def test_every_core_type_is_always_included_regardless_of_prompt():
    shortlist = set(select_candidate_node_types("do literally anything unrelated", MANIFEST))
    assert CORE_TYPES <= shortlist
    assert {"Literal", "Echo"} <= shortlist


def test_human_approval_request_surfaces_human_in_loop():
    # HumanInLoopAgent is a core type, so this is always true — but it's the
    # task's own worked example, so it's worth asserting explicitly.
    shortlist = select_candidate_node_types(
        "Analyse a customer request and pause for human approval before continuing.",
        MANIFEST,
    )
    assert "HumanInLoopAgent" in shortlist


def test_search_internal_knowledge_surfaces_a_retrieval_type():
    shortlist = select_candidate_node_types(
        "Search our internal knowledge base and prior project documents for relevant evidence.",
        MANIFEST,
    )
    retrieval_types = {"KnowledgeRetrieval", "RAGAgent", "InternalProjectEvidenceRetrieverAgent", "PriorProjectRetrieverAgent"}
    assert retrieval_types & set(shortlist)


def test_route_based_on_category_surfaces_router():
    shortlist = select_candidate_node_types(
        "Route the incoming request to the right team based on its category.",
        MANIFEST,
    )
    assert "RouterAgent" in shortlist  # always true (core), asserted for documentation


def test_create_pdf_surfaces_pdf_rendering_capability():
    shortlist = select_candidate_node_types(
        "Draft the content and then create a PDF proposal document from it.",
        MANIFEST,
    )
    assert "PDFProposalRenderer" in shortlist


def test_shortlist_is_materially_smaller_than_the_full_manifest_for_a_narrow_prompt():
    shortlist = select_candidate_node_types("Route the request to the right team.", MANIFEST)
    assert len(shortlist) < len(MANIFEST)


def test_shortlist_never_contains_a_type_name_outside_the_registry():
    shortlist = select_candidate_node_types("summarize a customer email", MANIFEST)
    real_type_names = {e["type_name"] for e in MANIFEST}
    assert set(shortlist) <= real_type_names


def test_deterministic_for_the_same_inputs():
    prompt = "Search the web for competitor pricing and draft a memo."
    assert select_candidate_node_types(prompt, MANIFEST) == select_candidate_node_types(prompt, MANIFEST)
