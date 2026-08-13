"""Pydantic models for the retrieval pipeline.

These are the only types the outside world (RAG Agent node, API, Cockpit,
audit log) ever sees. Weaviate-specific types stay inside hybrid_search.py.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---- Enums -------------------------------------------------------------


# ---- Typed metadata filters ---------------------------------------------
#
# These are validated against a Collection's metadata_schema before they
# ever reach Weaviate. They compose with, but never replace, the immutable
# security clauses (owner scope, collection, resolved index) built in
# app.retrieval.filters.build_secure_where_filter.

METADATA_FILTER_OPERATORS = (
    "equals", "not_equals", "in", "not_in",
    "contains_any", "greater_than", "less_than", "between",
)


class MetadataFilterPredicate(BaseModel):
    field: str = Field(min_length=1)
    operator: Literal[
        "equals", "not_equals", "in", "not_in",
        "contains_any", "greater_than", "less_than", "between",
    ] = "equals"
    value: Any = None


class MetadataFilterGroup(BaseModel):
    logic: Literal["and", "or"] = "and"
    predicates: list[MetadataFilterPredicate] = Field(default_factory=list)
    groups: list["MetadataFilterGroup"] = Field(default_factory=list)


MetadataFilterGroup.model_rebuild()


# ---- Input side ---------------------------------------------------------

class RetrievalFilters(BaseModel):
    """Metadata pre-filter applied BEFORE hybrid search runs in Weaviate.

    session_id is mandatory: it is the security boundary enforced at the
    retrieval layer (see docs/security.md and Phase 11 isolation verifier).
    Every chunk in Weaviate carries a session_id; every query pins one.

    ``metadata`` carries the typed, schema-validated user/self-query filter
    tree. It is ANDed with the immutable security clauses above it — it can
    never widen access, only narrow the candidate set further.
    """

    session_id: str = Field(..., min_length=1)
    collection_id: str = Field(..., min_length=1)
    industry: Optional[str] = None
    doc_types: Optional[list[str]] = None
    collection_ids: Optional[list[str]] = None
    date_after: Optional[date] = None
    date_before: Optional[date] = None
    metadata: Optional[MetadataFilterGroup] = None

    def user_filter_dump(self) -> dict[str, Any]:
        """Trace-safe view of caller-supplied filters (no security clauses)."""

        return self.metadata.model_dump(mode="json") if self.metadata else {}


class RetrievalQuery(BaseModel):
    """Single retrieval request. Defaults come from app.config."""

    query: str = Field(..., min_length=1)
    filters: RetrievalFilters

    # Saved-resource resolution — set by callers that resolve through a
    # Collection/Retrieval Profile/Index rather than passing raw knobs.
    retrieval_profile_id: Optional[str] = None
    retrieval_profile_version: Optional[int] = None
    index_id: Optional[str] = None
    rag_agent_id: Optional[str] = None

    # Pipeline knobs — all defaults match app/config.py. Bounds are wide
    # enough to cover both the legacy inline RAGAgent path (top_k<=100,
    # top_n<=20) and the Playground/API path (top_k<=200, top_n<=50).
    top_k_candidates: int = Field(default=25, ge=1, le=200)
    top_n_final: int = Field(default=8, ge=1, le=50)
    alpha: float = Field(default=0.5, ge=0.0, le=1.0)  # 0=BM25, 1=vector
    strategy: Literal["dense", "sparse", "hybrid", "hybrid_rerank"] = "hybrid"
    fusion_strategy: Literal["relative_score", "rrf"] = "relative_score"

    # Stage toggles — let callers (and the eval lab) ablate stages.
    rewrite_query: bool = True
    rerank: bool = True
    compress: bool = True
    query_transform: Literal[
        "none", "rewrite", "multi_query", "decomposition", "hyde", "self_query"
    ] = "none"
    context_expansion: Literal["none", "parent", "sentence_window", "contextual"] = "none"
    diagnostic_components: bool = True


# ---- Output side --------------------------------------------------------

class RetrievedChunk(BaseModel):
    """One chunk that survived the pipeline.

    Score fields are progressively filled in:
      stage 2 (hybrid_search)  -> hybrid_score (dense_score/sparse_score/fusion_score
                                   when diagnostic_components requested them)
      stage 3 (reranker)       -> rerank_score, rerank_reason
      stage 4 (compressor)     -> compressed_text
    """

    chunk_id: str
    display_number: Optional[int] = None   # global citation [N] stamped at ingestion
    doc_id: str
    doc_title: str
    doc_type: str
    text: str                              # raw chunk from Weaviate
    metadata: dict = Field(default_factory=dict)  # industry, doc_date, source_uri, etc.

    # Retrieval-time content surfaces. `retrieval_content` is what got
    # embedded/searched (may carry contextual enrichment); `text` stays the
    # immutable raw chunk; `context_content` is the sentence-window surround.
    retrieval_content: Optional[str] = None
    context_content: Optional[str] = None

    hybrid_score: float = 0.0
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    fusion_score: Optional[float] = None
    rerank_score: Optional[float] = None
    rerank_reason: Optional[str] = None    # shown in the Cockpit
    compressed_text: Optional[str] = None  # what the drafter actually sees
    rank: Optional[int] = None

    # Knowledge Studio provenance — resolves a chunk back to its owning
    # document, source version and index without a second lookup.
    document_id: Optional[str] = None
    source_id: Optional[str] = None
    source_version_id: Optional[str] = None
    index_id: Optional[str] = None
    parent_chunk_id: Optional[str] = None
    expanded_from_chunk_id: Optional[str] = None
    page: Optional[int] = None
    section: Optional[str] = None


class RetrievalStage(BaseModel):
    """One named step in the staged retrieval pipeline, for the trace."""

    name: str
    duration_ms: float = 0.0
    input_count: Optional[int] = None
    output_count: Optional[int] = None
    details: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    """Result of one retrieve() call. This is what the RAG Agent node returns
    in the workflow state and what the Cockpit displays in the retrievals
    panel."""

    query: str
    rewritten_query: Optional[str]
    transformed_queries: list[str] = Field(default_factory=list)
    chunks: list[RetrievedChunk]
    candidates: list[RetrievedChunk] = Field(default_factory=list)
    filters_applied: RetrievalFilters
    timings_ms: dict[str, float]           # per-stage latency, e.g.
                                           # {"hybrid_search_ms": 184.2,
                                           #  "rerank_ms": 920.5,
                                           #  "compress_ms": 410.1}

    retrieval_request_id: Optional[str] = None
    collection_id: Optional[str] = None
    resolved_index_id: Optional[str] = None
    retrieval_profile_id: Optional[str] = None
    retrieval_profile_version: Optional[int] = None
    stages: list[RetrievalStage] = Field(default_factory=list)
    final_context: str = ""
    context_token_count: int = 0
    strategy: str = "hybrid"
    resolved_resources: dict[str, Any] = Field(default_factory=dict)
