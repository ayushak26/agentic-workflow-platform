"""Saved RAG Agent query request/response contracts."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RAGCitation(BaseModel):
    label: int                          # the [N] used in the generated answer
    chunk_id: str
    document_id: str | None = None
    source_id: str | None = None
    source_version_id: str | None = None
    filename: str
    page: int | None = None
    section: str | None = None
    snippet: str
    evidence_status: str = "retrieved_not_verified"


class RAGQueryResponse(BaseModel):
    request_id: str
    rag_agent_id: str
    collection_id: str
    index_id: str
    retrieval_profile_id: str
    retrieval_profile_version: int
    generation_profile_id: str
    generation_profile_version: int
    query: str = ""
    answer: str
    citations: list[RAGCitation]
    sources: list[dict[str, Any]] = Field(default_factory=list)
    relevant_context: list[dict[str, Any]] = Field(default_factory=list)
    configured_answering_model: str = ""
    retrieved_chunks: list[dict[str, Any]]
    final_context: str
    retrieval_trace_id: str
    candidate_count: int
    context_count: int
    timings_ms: dict[str, float]
    resolved_resources: dict[str, Any] = Field(default_factory=dict)
    generation: dict[str, Any] = Field(default_factory=dict)
