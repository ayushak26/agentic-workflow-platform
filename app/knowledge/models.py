"""Versioned Knowledge Studio resource contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.model_catalog import AUTO_MODEL


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResourceStatus(str, Enum):
    DRAFT = "draft"
    BUILDING = "building"
    READY = "ready"
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ARCHIVED = "archived"


class ProfileType(str, Enum):
    PARSER = "parser"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    RETRIEVAL = "retrieval"
    ROUTING = "routing"
    RERANKER = "reranker"
    GENERATION = "generation"
    INGESTION_PRESET = "ingestion_preset"
    RETRIEVAL_PRESET = "retrieval_preset"


class IngestionJobStatus(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    PARSING = "parsing"
    CHUNKING = "chunking"
    ENRICHING = "enriching"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScopedResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    owner_scope_id: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CollectionResource(ScopedResource):
    collection_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    status: ResourceStatus = ResourceStatus.DRAFT
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    active_index_id: str | None = None
    metadata_schema: dict[str, Any] = Field(default_factory=dict)
    doc_types: list[str] = Field(default_factory=lambda: ["general"])
    default_industry: str | None = None
    legacy_aliases: list[str] = Field(default_factory=list)
    schema_version: int = 2

    @property
    def display_name(self) -> str:
        return self.name


class DocumentResource(ScopedResource):
    document_id: str
    collection_id: str
    source_id: str
    filename: str
    mime_type: str
    source_format: str
    status: ResourceStatus = ResourceStatus.DRAFT
    current_source_version_id: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class SourceVersionResource(ScopedResource):
    source_version_id: str
    source_id: str
    document_id: str
    collection_id: str
    version: int = Field(ge=1)
    filename: str
    mime_type: str
    source_format: str
    storage_key: str
    content_hash: str
    byte_size: int = Field(ge=0)
    immutable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParserProfileConfig(BaseModel):
    strategy: Literal["standard", "layout_aware", "structure_aware", "ocr_fallback"] = "standard"
    ocr_min_text_characters: int = Field(default=80, ge=0)
    preserve_tables: bool = True
    preserve_headings: bool = True


class ChunkingProfileConfig(BaseModel):
    strategy: Literal[
        "fixed_token",
        "recursive",
        "structure_aware",
        "parent_child",
        "contextual",
        "semantic",
        "sentence_window",
    ] = "recursive"
    target_tokens: int = Field(default=512, ge=32, le=8192)
    max_tokens: int = Field(default=1024, ge=64, le=16384)
    overlap_tokens: int = Field(default=64, ge=0, le=2048)
    min_tokens: int = Field(default=50, ge=1, le=4096)
    parent_tokens: int = Field(default=1536, ge=128, le=16384)
    sentence_window: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_token_windows(self) -> "ChunkingProfileConfig":
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens cannot exceed max_tokens")
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        return self


class EmbeddingProfileConfig(BaseModel):
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    dimensions: int = Field(default=1536, ge=64, le=65536)
    batch_size: int = Field(default=64, ge=1, le=2048)
    data_processing: Literal["external", "private", "local"] = "external"


class RetrievalProfileConfig(BaseModel):
    strategy: Literal["dense", "sparse", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    candidate_count: int = Field(default=20, ge=1, le=200)
    final_count: int = Field(default=6, ge=1, le=50)
    alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    fusion_strategy: Literal["relative_score", "rrf"] = "relative_score"
    reranking_enabled: bool = True
    compression_enabled: bool = True
    query_transform: Literal["none", "rewrite", "multi_query", "decomposition", "hyde", "self_query"] = "none"
    context_expansion: Literal["none", "parent", "sentence_window", "contextual"] = "none"
    sentence_window: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_counts(self) -> "RetrievalProfileConfig":
        if self.final_count > self.candidate_count:
            raise ValueError("final_count cannot exceed candidate_count")
        return self


class RetrievalRoute(BaseModel):
    route_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    collection_id: str
    retrieval_profile_id: str
    retrieval_profile_version: int = Field(ge=1)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    description: str = ""


class RetrievalRoutingProfileConfig(BaseModel):
    mode: Literal["deterministic", "ai", "hybrid"] = "deterministic"
    routes: list[RetrievalRoute] = Field(min_length=1, max_length=25)
    default_route_id: str | None = None

    @model_validator(mode="after")
    def validate_routes(self) -> "RetrievalRoutingProfileConfig":
        ids = [route.route_id for route in self.routes]
        if len(ids) != len(set(ids)):
            raise ValueError("routing route_id values must be unique")
        if self.default_route_id is not None and self.default_route_id not in ids:
            raise ValueError("default_route_id must identify one configured route")
        return self


class GenerationProfileConfig(BaseModel):
    model: str = AUTO_MODEL
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    instruction: str = (
        "Answer using only the provided sources. Cite every factual claim. "
        "If the sources do not contain the answer, say that it was not found."
    )
    citation_policy: Literal["required", "optional", "none"] = "required"
    no_answer_policy: Literal["say_not_found", "return_empty", "request_clarification"] = "say_not_found"


class ProfileVersion(ScopedResource):
    profile_id: str
    profile_type: ProfileType
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    strategy: str
    config: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    status: ResourceStatus = ResourceStatus.ACTIVE
    based_on_preset: str | None = None


class IndexVersion(ScopedResource):
    index_id: str
    collection_id: str
    version: int = Field(ge=1)
    parser_profile_id: str
    parser_profile_version: int = Field(ge=1)
    chunking_profile_id: str
    chunking_profile_version: int = Field(ge=1)
    embedding_profile_id: str
    embedding_profile_version: int = Field(ge=1)
    status: ResourceStatus = ResourceStatus.BUILDING
    physical_store: str = "weaviate"
    physical_collection: str = "DocumentChunk"
    # Logical collection IDs may be upgraded while legacy objects retain the
    # original filter value. New indexes use the logical ID for both fields.
    physical_collection_id: str | None = None
    # Legacy objects predate index_id. A null value intentionally omits that
    # filter while still pinning owner and physical collection scope.
    physical_index_id: str | None = None
    embedding_fingerprint: str
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    activated_at: datetime | None = None


class IngestionSourceInput(BaseModel):
    filename: str
    storage_key: str
    mime_type: str
    content_hash: str
    byte_size: int = Field(ge=0)


class IngestionJob(ScopedResource):
    ingestion_job_id: str
    collection_id: str
    parser_profile_id: str
    parser_profile_version: int = Field(ge=1)
    chunking_profile_id: str
    chunking_profile_version: int = Field(ge=1)
    embedding_profile_id: str
    embedding_profile_version: int = Field(ge=1)
    target_index_id: str
    status: IngestionJobStatus = IngestionJobStatus.QUEUED
    documents_total: int = Field(default=0, ge=0)
    documents_processed: int = Field(default=0, ge=0)
    documents_failed: int = Field(default=0, ge=0)
    chunks_created: int = Field(default=0, ge=0)
    current_document_id: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    source_inputs: list[IngestionSourceInput] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


class RAGAgentDefinition(ScopedResource):
    rag_agent_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    collection_id: str
    retrieval_profile_id: str
    retrieval_profile_version: int | None = None
    generation_profile_id: str
    generation_profile_version: int | None = None
    routing_profile_id: str | None = None
    routing_profile_version: int | None = None
    status: ResourceStatus = ResourceStatus.ACTIVE


class RetrievalTrace(ScopedResource):
    retrieval_request_id: str
    rag_agent_id: str | None = None
    retrieval_profile_id: str
    retrieval_profile_version: int
    collection_id: str
    resolved_index_id: str
    parser_profile_id: str | None = None
    parser_profile_version: int | None = None
    chunking_profile_id: str | None = None
    chunking_profile_version: int | None = None
    embedding_profile_id: str | None = None
    embedding_profile_version: int | None = None
    original_query: str
    transformed_queries: list[str] = Field(default_factory=list)
    security_filters: dict[str, Any] = Field(default_factory=dict)
    user_filters: dict[str, Any] = Field(default_factory=dict)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_context: list[dict[str, Any]] = Field(default_factory=list)
    final_context: str = ""
    context_token_count: int = 0
    timings_ms: dict[str, float] = Field(default_factory=dict)
    model_usage: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["completed", "failed"] = "completed"
    error: str | None = None
    expires_at: datetime | None = None
