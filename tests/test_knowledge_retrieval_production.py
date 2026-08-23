"""Production-boundary tests for the Knowledge Retrieval workflow node."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.knowledge.models import ProfileType, ResourceStatus
from app.config import settings
from app.knowledge.repository import ResourceNotFoundError
from app.nodes.knowledge_retrieval import KnowledgeRetrieval, KnowledgeRetrievalError
from app.retrieval.models import RetrievalFilters, RetrievalResult, RetrievedChunk


class Repository:
    def __init__(self, *, status=ResourceStatus.READY, exists=True):
        self.status = status
        self.exists = exists
        self.scopes: list[str] = []

    async def get_collection(self, scope, collection_id):
        self.scopes.append(scope)
        if not self.exists:
            raise ResourceNotFoundError(collection_id)
        return SimpleNamespace(
            collection_id=collection_id, name="Policies", status=self.status,
            active_index_id="idx-1",
        )

    async def get_profile(self, scope, profile_id, version, profile_type):
        self.scopes.append(scope)
        assert profile_type == ProfileType.RETRIEVAL
        return SimpleNamespace(profile_id=profile_id, status=ResourceStatus.ACTIVE)


class Retrieval:
    def __init__(self, result=None, errors=None):
        self.result = result
        self.errors = list(errors or [])
        self.calls = 0

    async def retrieve(self, request, *, owner_scope_id, llm=None):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


class DelayedRetrieval(Retrieval):
    def __init__(self, delay: float, result_value):
        super().__init__(result_value)
        self.delay = delay

    async def retrieve(self, request, *, owner_scope_id, llm=None):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.result


def result(*, chunks=True):
    item = RetrievedChunk(
        chunk_id="chunk-1", doc_id="doc-1", document_id="doc-1",
        source_version_id="source-v1", doc_title="Policy", doc_type="policy",
        text="The verification code is KS-4827.", page=4,
        metadata={"source_uri": "knowledge://policy"},
    )
    filters = RetrievalFilters(session_id="tenant-a", collection_id="col-1")
    return RetrievalResult(
        query="verification code", rewritten_query=None,
        chunks=[item] if chunks else [], candidates=[item] if chunks else [],
        filters_applied=filters, timings_ms={"total_ms": 4.0},
        retrieval_request_id="rr-1", resolved_index_id="idx-1",
        final_context=item.text if chunks else "", resolved_resources={"index_id": "idx-1"},
    )


def node(repository, retrieval):
    return KnowledgeRetrieval("knowledge", {
        "collection_id": "col-1", "retrieval_profile_id": "rp-1",
        "query": "verification code",
    }, services={"knowledge_repository": repository, "retrieval_service": retrieval})


@pytest.mark.asyncio
async def test_runtime_revalidates_scope_and_preserves_real_provenance():
    repository = Repository()
    output = await node(repository, Retrieval(result())).run(
        {"session_id": "tenant-a"}, {
            "collection_id": "col-1", "retrieval_profile_id": "rp-1",
            "query": "verification code",
        },
    )
    assert repository.scopes == ["tenant-a", "tenant-a"]
    assert output["status"] == "success"
    assert output["context"] == "The verification code is KS-4827."
    assert output["citations"][0] == {
        "document_id": "doc-1", "source_version_id": "source-v1",
        "chunk_id": "chunk-1", "filename": "Policy", "page": 4,
        "section": None, "evidence_status": "retrieved_not_verified",
    }


@pytest.mark.asyncio
async def test_deleted_collection_fails_before_provider_request():
    retrieval = Retrieval(result())
    with pytest.raises(KnowledgeRetrievalError, match="COLLECTION_NOT_FOUND"):
        await node(Repository(exists=False), retrieval).run(
            {"session_id": "tenant-a"}, {
                "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "q",
            },
        )
    assert retrieval.calls == 0


@pytest.mark.asyncio
async def test_not_ready_collection_fails_before_provider_request():
    with pytest.raises(KnowledgeRetrievalError, match="COLLECTION_NOT_READY"):
        await node(Repository(status=ResourceStatus.BUILDING), Retrieval(result())).run(
            {"session_id": "tenant-a"}, {
                "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "q",
            },
        )


@pytest.mark.asyncio
async def test_empty_retrieval_is_successful_no_results_not_provider_failure():
    output = await node(Repository(), Retrieval(result(chunks=False))).run(
        {"session_id": "tenant-a"}, {
            "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "unknown",
        },
    )
    assert output["status"] == "no_results"
    assert output["retrieved_chunks"] == []
    assert output["citations"] == []


@pytest.mark.asyncio
async def test_transient_failure_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.nodes.knowledge_retrieval.random.uniform", lambda *_: 0)
    retrieval = Retrieval(result(), errors=[ConnectionError("temporary")])
    output = await node(Repository(), retrieval).run(
        {"session_id": "tenant-a"}, {
            "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "q",
        },
    )
    assert output["status"] == "success"
    assert retrieval.calls == 2


@pytest.mark.asyncio
async def test_retrieval_uses_dedicated_pipeline_deadline_not_generic_io_timeout(monkeypatch):
    monkeypatch.setattr(settings, "external_request_timeout_seconds", 0.001)
    monkeypatch.setattr(settings, "knowledge_retrieval_timeout_seconds", 0.05)
    retrieval = DelayedRetrieval(0.01, result())

    output = await node(Repository(), retrieval).run(
        {"session_id": "tenant-a"}, {
            "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "q",
        },
    )

    assert output["status"] == "success"
    assert retrieval.calls == 1


@pytest.mark.asyncio
async def test_dedicated_retrieval_deadline_retries_once_then_returns_stable_timeout(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_retrieval_timeout_seconds", 0.001)
    monkeypatch.setattr("app.nodes.knowledge_retrieval.random.uniform", lambda *_: 0)
    retrieval = DelayedRetrieval(0.01, result())

    with pytest.raises(KnowledgeRetrievalError, match="RETRIEVAL_TIMEOUT"):
        await node(Repository(), retrieval).run(
            {"session_id": "tenant-a"}, {
                "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "q",
            },
        )

    assert retrieval.calls == 2


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_into_a_provider_error():
    class Cancelled:
        async def retrieve(self, *args, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await node(Repository(), Cancelled()).run(
            {"session_id": "tenant-a"}, {
                "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "q",
            },
        )


@pytest.mark.asyncio
async def test_malformed_retrieval_response_fails_with_stable_error():
    """Provider/adapter contract drift must not escape as an arbitrary exception."""

    with pytest.raises(KnowledgeRetrievalError, match="RETRIEVAL_ERROR"):
        await node(Repository(), Retrieval({"chunks": "not-a-list"})).run(
            {"session_id": "tenant-a"}, {
                "collection_id": "col-1", "retrieval_profile_id": "rp-1", "query": "q",
            },
        )