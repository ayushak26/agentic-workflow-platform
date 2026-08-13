"""Security-boundary tests for the Knowledge Studio retrieval/RAG surface.

Covers the properties the engineering report calls mandatory: retrieval
authorization scope can't be overridden, a runtime filter can't touch a
reserved security/provenance field, and the RBAC roles actually carry the
new permissions.
"""
from __future__ import annotations

import pytest

from app.retrieval.models import RetrievalFilters, RetrievalQuery
from app.retrieval.service import RetrievalAuthorizationError, RetrievalService
from app.runtime.preflight import preflight_workflow_yaml
from app.security.rbac import ROLE_PERMISSIONS, Role, has_permission


def _yaml_for(config_lines: str, node_type: str) -> str:
    return f"""
name: Knowledge Security Test
inputs:
  question:
    type: text
    required: true
nodes:
  - id: knowledge_node
    type: {node_type}
    config:
{config_lines}
entry: knowledge_node
exit: knowledge_node
"""


def codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


# ---- RetrievalService: authorization scope cannot be overridden ------------

@pytest.mark.asyncio
async def test_retrieve_rejects_a_request_whose_session_id_does_not_match_owner_scope():
    service = RetrievalService(weaviate_client=None, embedder=None, llm=None, repository=None)
    query = RetrievalQuery(
        query="how is Dura 25 cleaned",
        filters=RetrievalFilters(session_id="attacker-session", collection_id="col-1"),
    )
    with pytest.raises(RetrievalAuthorizationError):
        await service.retrieve(query, owner_scope_id="victim-session")


@pytest.mark.asyncio
async def test_retrieve_call_operator_pins_owner_scope_from_the_request_itself():
    """The __call__ shortcut used by the legacy retriever must not let a
    caller retrieve as someone else either — it derives owner_scope_id from
    the same filters.session_id it then checks against."""
    service = RetrievalService(weaviate_client=None, embedder=None, llm=None, repository=None)
    query = RetrievalQuery(
        query="q", filters=RetrievalFilters(session_id="s-1", collection_id="col-1"),
    )
    # No repository/client configured -> if authorization passed, this would
    # fail later with an AttributeError/None-call, not an auth error. It must
    # not raise RetrievalAuthorizationError, because session_id matches itself.
    with pytest.raises(Exception) as excinfo:
        await service(query)
    assert not isinstance(excinfo.value, RetrievalAuthorizationError)


# ---- Preflight: runtime_filters cannot set a reserved field -----------------

@pytest.mark.parametrize("node_type,extra_config", [
    ("RAGAgent", "      query: \"{{inputs.question}}\"\n      rag_agent_id: rag_test\n"),
    ("KnowledgeRetrieval", "      query: \"{{inputs.question}}\"\n      collection_id: col_test\n      retrieval_profile_id: retprof_test\n"),
])
def test_preflight_blocks_a_runtime_filter_that_sets_a_reserved_field(node_type, extra_config):
    config = extra_config + "      runtime_filters:\n        session_id: attacker-controlled\n"
    report = preflight_workflow_yaml(_yaml_for(config, node_type))
    assert "RAG_RUNTIME_FILTER_UNSAFE" in codes(report)


def test_preflight_allows_a_runtime_filter_on_an_ordinary_metadata_field():
    config = (
        "      query: \"{{inputs.question}}\"\n"
        "      collection_id: col_test\n"
        "      retrieval_profile_id: retprof_test\n"
        "      runtime_filters:\n"
        "        product: Dura 25\n"
    )
    report = preflight_workflow_yaml(_yaml_for(config, "KnowledgeRetrieval"))
    assert "RAG_RUNTIME_FILTER_UNSAFE" not in codes(report)


def test_preflight_blocks_combining_rag_agent_id_with_legacy_retrieval_knobs():
    config = (
        "      query: \"{{inputs.question}}\"\n"
        "      rag_agent_id: rag_test\n"
        "      alpha: 0.9\n"
    )
    report = preflight_workflow_yaml(_yaml_for(config, "RAGAgent"))
    assert "NODE_CONFIG_INVALID" in codes(report)


# ---- RBAC: every role that can touch Knowledge Studio has the right grants --

def test_admin_and_consultant_have_full_knowledge_and_rag_permissions():
    for role in (Role.ADMIN, Role.CONSULTANT):
        for permission in ("knowledge:read", "knowledge:write", "rag:query", "rag:write"):
            assert has_permission(role, permission), f"{role} missing {permission}"


def test_viewer_can_read_and_query_but_not_write():
    assert has_permission(Role.VIEWER, "knowledge:read")
    assert has_permission(Role.VIEWER, "rag:query")
    assert not has_permission(Role.VIEWER, "knowledge:write")
    assert not has_permission(Role.VIEWER, "rag:write")


def test_every_role_has_a_defined_permission_set():
    for role in Role:
        assert role in ROLE_PERMISSIONS
