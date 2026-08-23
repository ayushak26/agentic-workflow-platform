"""RAGService.query() -- runtime_context, richer output, and Auto gating.

Builds a real saved RAG Agent (collection + retrieval/generation profiles)
through KnowledgeRepository/KnowledgeService backed by the in-memory fake
Mongo already used elsewhere (tests/fake_mongo.py), with a fake
retrieval_service (the hybrid-search pipeline is out of scope here --
tests/test_retrieval_pipeline_stages.py covers it) and a fake llm gateway
that records how it was called.
"""
from __future__ import annotations

import pytest

from app.knowledge.models import ProfileType, ResourceStatus
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.service import KnowledgeService
from app.llm.base import LLMResponse
from app.llm.model_catalog import AUTO_MODEL
from app.rag.service import RAGService
from app.retrieval.models import RetrievalFilters, RetrievalResult, RetrievedChunk
from tests.fake_mongo import InMemoryDB


class FakeRetrievalService:
    def __init__(self, chunks: list[RetrievedChunk]):
        self.chunks = chunks
        self.calls: list[dict] = []

    async def retrieve(self, request, *, owner_scope_id, llm=None):
        self.calls.append({"request": request, "owner_scope_id": owner_scope_id})
        return RetrievalResult(
            query=request.query,
            rewritten_query=None,
            chunks=self.chunks,
            candidates=self.chunks,
            filters_applied=RetrievalFilters(
                session_id=owner_scope_id, collection_id=request.filters.collection_id
            ),
            timings_ms={"total_ms": 12.0},
            retrieval_request_id="",
            final_context="\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(self.chunks)),
        )


class FakeLLM:
    """Records calls and whether ensure_routed() was invoked, without
    depending on RegistryLLMGateway's real provider/availability logic."""

    def __init__(self, response_text: str = "The answer is [1]."):
        self.response_text = response_text
        self.routed = False
        self.calls: list[dict] = []

    def ensure_routed(self, *, node_type=None):
        self.routed = True
        return self

    async def complete(self, *, model, system, user, temperature, stage):
        self.calls.append({
            "model": model, "system": system, "user": user,
            "temperature": temperature, "stage": stage,
        })
        return LLMResponse(text=self.response_text, model="claude-opus-5", input_tokens=10, output_tokens=5)


def _chunk(chunk_id="chunk-1", text="Q3 revenue increased 14% year over year.") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id="doc-1",
        doc_title="Q3 Sales Report.pdf",
        doc_type="pdf",
        text=text,
        metadata={"source_uri": "https://example.com/q3.pdf"},
        hybrid_score=0.93,
        document_id="document-1",
        source_id="source-1",
        page=7,
    )


async def _build_agent(*, owner_scope_id: str, generation_model: str, repository: KnowledgeRepository | None = None):
    service = KnowledgeService(repository or KnowledgeRepository(InMemoryDB()))
    collection = await service.create_collection(owner_scope_id=owner_scope_id, name="Finance Knowledge")
    parser = await service.create_profile_version(
        owner_scope_id=owner_scope_id, profile_type=ProfileType.PARSER, name="P", strategy="standard", config={},
    )
    chunking = await service.create_profile_version(
        owner_scope_id=owner_scope_id, profile_type=ProfileType.CHUNKING, name="C", strategy="recursive", config={},
    )
    embedding = await service.create_profile_version(
        owner_scope_id=owner_scope_id, profile_type=ProfileType.EMBEDDING, name="E", strategy="openai", config={},
    )
    index = await service.create_index(
        owner_scope_id=owner_scope_id, collection_id=collection.collection_id,
        parser_profile_id=parser.profile_id, parser_profile_version=parser.version,
        chunking_profile_id=chunking.profile_id, chunking_profile_version=chunking.version,
        embedding_profile_id=embedding.profile_id, embedding_profile_version=embedding.version,
    )
    index.status = ResourceStatus.READY
    await service.repository.save_index(index)
    await service.activate_index(
        owner_scope_id=owner_scope_id, collection_id=collection.collection_id, index_id=index.index_id
    )
    retrieval_profile = await service.create_profile_version(
        owner_scope_id=owner_scope_id, profile_type=ProfileType.RETRIEVAL, name="R", strategy="hybrid", config={},
    )
    generation_profile = await service.create_profile_version(
        owner_scope_id=owner_scope_id, profile_type=ProfileType.GENERATION, name="G", strategy="grounded",
        config={"model": generation_model},
    )
    agent = await service.create_rag_agent(
        owner_scope_id=owner_scope_id, name="Finance Analyst", collection_id=collection.collection_id,
        retrieval_profile_id=retrieval_profile.profile_id, generation_profile_id=generation_profile.profile_id,
    )
    return service.repository, agent


@pytest.mark.asyncio
async def test_auto_model_routes_through_ensure_routed():
    repository, agent = await _build_agent(owner_scope_id="owner-a", generation_model=AUTO_MODEL)
    llm = FakeLLM()
    rag_service = RAGService(
        repository=repository, retrieval_service=FakeRetrievalService([_chunk()]), llm=llm,
    )

    response = await rag_service.query(
        owner_scope_id="owner-a", rag_agent_id=agent.rag_agent_id, query="What was Q3 revenue growth?", llm=llm,
    )

    assert llm.routed is True
    assert response.configured_answering_model == "auto"
    assert response.generation["model"] == "claude-opus-5"


@pytest.mark.asyncio
async def test_explicit_model_does_not_call_ensure_routed():
    repository, agent = await _build_agent(owner_scope_id="owner-a", generation_model="claude-sonnet-4-5")
    llm = FakeLLM()
    rag_service = RAGService(
        repository=repository, retrieval_service=FakeRetrievalService([_chunk()]), llm=llm,
    )

    await rag_service.query(
        owner_scope_id="owner-a", rag_agent_id=agent.rag_agent_id, query="What was Q3 revenue growth?", llm=llm,
    )

    assert llm.routed is False
    assert llm.calls[0]["model"] == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_runtime_context_is_a_distinct_prompt_block_from_retrieved_sources():
    repository, agent = await _build_agent(owner_scope_id="owner-a", generation_model="claude-sonnet-4-5")
    llm = FakeLLM()
    rag_service = RAGService(
        repository=repository, retrieval_service=FakeRetrievalService([_chunk()]), llm=llm,
    )

    await rag_service.query(
        owner_scope_id="owner-a", rag_agent_id=agent.rag_agent_id,
        query="What should we tell this customer?",
        runtime_context={"customer_name": "Acme", "plan": "Enterprise"},
        llm=llm,
    )

    prompt = llm.calls[0]["user"]
    assert "RUNTIME CONTEXT" in prompt
    assert "Acme" in prompt
    assert "SOURCES:" in prompt
    assert prompt.index("RUNTIME CONTEXT") < prompt.index("SOURCES:")


@pytest.mark.asyncio
async def test_runtime_context_omitted_when_not_supplied():
    repository, agent = await _build_agent(owner_scope_id="owner-a", generation_model="claude-sonnet-4-5")
    llm = FakeLLM()
    rag_service = RAGService(
        repository=repository, retrieval_service=FakeRetrievalService([_chunk()]), llm=llm,
    )

    await rag_service.query(
        owner_scope_id="owner-a", rag_agent_id=agent.rag_agent_id, query="What was Q3 revenue growth?", llm=llm,
    )

    assert "RUNTIME CONTEXT" not in llm.calls[0]["user"]


@pytest.mark.asyncio
async def test_selected_document_ids_are_enforced_as_retrieval_scope():
    repository, agent = await _build_agent(owner_scope_id="owner-a", generation_model="claude-sonnet-4-5")
    retrieval = FakeRetrievalService([_chunk()])
    llm = FakeLLM()
    rag_service = RAGService(repository=repository, retrieval_service=retrieval, llm=llm)

    await rag_service.query(
        owner_scope_id="owner-a", rag_agent_id=agent.rag_agent_id,
        query="What was Q3 revenue growth?", document_ids=["document-1"], llm=llm,
    )

    assert retrieval.calls[0]["request"].filters.document_ids == ["document-1"]


@pytest.mark.asyncio
async def test_response_includes_deduplicated_sources_and_relevant_context():
    repository, agent = await _build_agent(owner_scope_id="owner-a", generation_model="claude-sonnet-4-5")
    llm = FakeLLM(response_text="Revenue increased 14% year over year in Q3. [1]")
    rag_service = RAGService(
        repository=repository, retrieval_service=FakeRetrievalService([_chunk()]), llm=llm,
    )

    response = await rag_service.query(
        owner_scope_id="owner-a", rag_agent_id=agent.rag_agent_id, query="What was Q3 revenue growth?", llm=llm,
    )

    assert len(response.sources) == 1
    source = response.sources[0]
    assert source["file_name"] == "Q3 Sales Report.pdf"
    assert source["locations"] == [{"page": 7, "section": None}]
    assert source["metadata"]["source_uri"] == "https://example.com/q3.pdf"

    assert len(response.relevant_context) == 1
    assert response.relevant_context[0]["content"] == "Q3 revenue increased 14% year over year."
    assert response.relevant_context[0]["page_no"] == 7


# ---- Search RAG agents by name (§7/§38, §42) -----------------------------

@pytest.mark.asyncio
async def test_list_rag_agents_search_is_case_insensitive_substring_match():
    repository, _ = await _build_agent(owner_scope_id="owner-a", generation_model="claude-sonnet-4-5")
    service = KnowledgeService(repository)
    await service.create_collection(owner_scope_id="owner-a", name="Support Knowledge")
    retrieval = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.RETRIEVAL, name="R2", strategy="hybrid", config={},
    )
    generation = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.GENERATION, name="G2", strategy="grounded", config={},
    )
    collection2 = (await repository.list_collections("owner-a"))[-1]
    await service.create_rag_agent(
        owner_scope_id="owner-a", name="Customer Support", collection_id=collection2.collection_id,
        retrieval_profile_id=retrieval.profile_id, generation_profile_id=generation.profile_id,
    )

    for needle in ("finance", "Finance", "FINANCE"):
        results = await repository.list_rag_agents("owner-a", search=needle)
        assert [a.name for a in results] == ["Finance Analyst"]

    assert len(await repository.list_rag_agents("owner-a")) == 2
    assert [a.name for a in await repository.list_rag_agents("owner-a", search="support")] == ["Customer Support"]


@pytest.mark.asyncio
async def test_list_rag_agents_is_workspace_scoped():
    repository, _ = await _build_agent(owner_scope_id="owner-a", generation_model="claude-sonnet-4-5")
    await _build_agent(owner_scope_id="owner-b", generation_model="claude-sonnet-4-5", repository=repository)

    owner_a_agents = await repository.list_rag_agents("owner-a", search="finance")
    owner_b_agents = await repository.list_rag_agents("owner-b", search="finance")

    assert len(owner_a_agents) == 1
    assert len(owner_b_agents) == 1
    assert owner_a_agents[0].rag_agent_id != owner_b_agents[0].rag_agent_id
