"""One generic retrieval-only workflow capability.

Retrieval strategy choices live in the saved Retrieval Profile.  This is the
only retrieval-only NodeType and complements, rather than duplicates,
``RAGAgent`` generation behavior.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.retrieval.filters import coerce_metadata_filter_group
from app.retrieval.models import RetrievalFilters, RetrievalQuery


class KnowledgeRetrievalConfig(BaseModel):
    collection_id: str = Field(json_schema_extra={"x-resource": "collection"})
    retrieval_profile_id: str = Field(json_schema_extra={"x-resource": "retrieval_profile"})
    query: str
    runtime_filters: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRetrievalInput(BaseModel):
    pass


class KnowledgeRetrievalOutput(BaseModel):
    retrieved_chunks: list[dict]
    citations: list[dict]
    context: str
    retrieval_trace_id: str
    collection_id: str
    resolved_index_id: str
    retrieval_profile_id: str
    candidate_count: int
    context_count: int
    timings_ms: dict[str, float]
    resolved_resources: dict[str, Any]


@NodeRegistry.register
class KnowledgeRetrieval(NodeType):
    type_name = "KnowledgeRetrieval"
    description = "Retrieve secured knowledge through a saved Retrieval Profile without generating an answer."
    input_schema = KnowledgeRetrievalInput
    output_schema = KnowledgeRetrievalOutput
    config_schema = KnowledgeRetrievalConfig

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"retrieval_service"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = KnowledgeRetrievalConfig.model_validate(resolved_config)
        metadata = coerce_metadata_filter_group(cfg.runtime_filters)
        result = await self.services["retrieval_service"].retrieve(
            RetrievalQuery(
                query=cfg.query,
                filters=RetrievalFilters(
                    session_id=state["session_id"],
                    collection_id=cfg.collection_id,
                    metadata=metadata,
                ),
                retrieval_profile_id=cfg.retrieval_profile_id,
            ),
            owner_scope_id=state["session_id"],
        )
        citations = [
            {
                "document_id": chunk.document_id,
                "source_version_id": chunk.source_version_id,
                "chunk_id": chunk.chunk_id,
                "filename": chunk.doc_title,
                "page": chunk.page,
                "section": chunk.section,
                "evidence_status": "retrieved_not_verified",
            }
            for chunk in result.chunks
        ]
        return {
            "retrieved_chunks": [chunk.model_dump(mode="json") for chunk in result.chunks],
            "citations": citations,
            "context": result.final_context,
            "retrieval_trace_id": result.retrieval_request_id or "",
            "collection_id": cfg.collection_id,
            "resolved_index_id": result.resolved_index_id or "",
            "retrieval_profile_id": cfg.retrieval_profile_id,
            "candidate_count": len(result.candidates),
            "context_count": len(result.chunks),
            "timings_ms": result.timings_ms,
            "resolved_resources": result.resolved_resources,
        }
