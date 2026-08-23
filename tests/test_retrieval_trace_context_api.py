from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from app.api.retrieval import get_trace_chunk_context
from app.knowledge.models import RetrievalTrace
from app.knowledge.repository import KnowledgeRepository
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from tests.fake_mongo import InMemoryDB


ALICE = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")
BOB = CurrentUser("bob", Role.CONSULTANT, session_id="bob-scope")


def request(repository: KnowledgeRepository) -> Request:
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services={
        "knowledge_repository": repository,
    }))))


@pytest.mark.asyncio
async def test_trace_chunk_context_returns_only_adjacent_same_document_passages():
    repository = KnowledgeRepository(InMemoryDB())
    await repository.save_trace(RetrievalTrace(
        workspace_id="alice-scope", owner_scope_id="alice-scope",
        retrieval_request_id="trace-1", retrieval_profile_id="profile-1",
        retrieval_profile_version=1, collection_id="collection-1", resolved_index_id="index-1",
        original_query="maintenance", selected_context=[
            {"chunk_id": "a", "document_id": "doc-1", "doc_title": "Manual.pdf", "text": "Previous", "page": 3},
            {"chunk_id": "b", "document_id": "doc-1", "doc_title": "Manual.pdf", "compressed_text": "Cited", "page": 4},
            {"chunk_id": "other", "document_id": "doc-2", "doc_title": "Other.pdf", "text": "Other document"},
            {"chunk_id": "c", "document_id": "doc-1", "doc_title": "Manual.pdf", "context_content": "Following", "page": 5},
        ],
    ))

    result = await get_trace_chunk_context("trace-1", "b", request(repository), ALICE)

    assert result["previous"]["text"] == "Previous"
    assert result["current"]["text"] == "Cited"
    assert result["next"]["text"] == "Following"


@pytest.mark.asyncio
async def test_trace_chunk_context_rejects_foreign_trace_and_nonmember_chunk():
    repository = KnowledgeRepository(InMemoryDB())
    await repository.save_trace(RetrievalTrace(
        workspace_id="alice-scope", owner_scope_id="alice-scope",
        retrieval_request_id="trace-1", retrieval_profile_id="profile-1",
        retrieval_profile_version=1, collection_id="collection-1", resolved_index_id="index-1",
        original_query="maintenance", selected_context=[{"chunk_id": "allowed", "document_id": "doc-1", "text": "Allowed"}],
    ))

    with pytest.raises(HTTPException) as foreign:
        await get_trace_chunk_context("trace-1", "allowed", request(repository), BOB)
    assert foreign.value.status_code == 404

    with pytest.raises(HTTPException) as missing:
        await get_trace_chunk_context("trace-1", "not-in-trace", request(repository), ALICE)
    assert missing.value.status_code == 404