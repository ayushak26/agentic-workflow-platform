"""Canonical retrieval path for workflows, RAG, Playground, MCP and Eval."""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.ingestion.embedder import Embedder, EmbedderConfig
from app.knowledge.ids import new_resource_id
from app.knowledge.models import ProfileType, RetrievalProfileConfig, RetrievalTrace
from app.knowledge.repository import KnowledgeRepository, ResourceNotFoundError
from app.observability.metrics import (
    RAG_CANDIDATES,
    RAG_CONTEXT_CHUNKS,
    RAG_RETRIEVAL_LATENCY,
    RAG_RETRIEVAL_REQUESTS,
)
from app.retrieval.compressor import compress_chunks
from app.retrieval.context import assemble_context, expand_context
from app.retrieval.filters import validate_metadata_filters
from app.retrieval.fusion import deduplicate, reciprocal_rank_fusion
from app.retrieval.models import (
    MetadataFilterGroup,
    RetrievalFilters,
    RetrievalQuery,
    RetrievalResult,
    RetrievalStage,
    RetrievedChunk,
)
from app.retrieval.query_transform import transform_query
from app.retrieval.reranker import rerank
from app.retrieval.strategies import dense_search, native_hybrid_search, sparse_search
from app.retrieval.weaviate_client import COLLECTION_NAME


class RetrievalAuthorizationError(PermissionError):
    """Exception raised for the RetrievalAuthorizationError case."""
    pass


class RetrievalCompatibilityError(ValueError):
    """Exception raised for the RetrievalCompatibilityError case."""
    pass


def _safe_candidate(chunk: RetrievedChunk) -> dict[str, Any]:
    """Trace score/provenance, not duplicate raw source text per stage."""

    return {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "source_version_id": chunk.source_version_id,
        "page": chunk.page,
        "section": chunk.section,
        "dense_score": chunk.dense_score,
        "sparse_score": chunk.sparse_score,
        "fusion_score": chunk.fusion_score,
        "rerank_score": chunk.rerank_score,
        "rerank_reason": chunk.rerank_reason,
        "rank": chunk.rank,
        "parent_chunk_id": chunk.parent_chunk_id,
        "expanded_from_chunk_id": chunk.expanded_from_chunk_id,
    }


class RetrievalService:
    """Provides the RetrievalService behaviour."""
    def __init__(
        self,
        *,
        weaviate_client: Any,
        embedder: Any,
        llm: Any,
        repository: KnowledgeRepository | None = None,
        collection_registry: Any | None = None,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        """Initialize the RetrievalService.

        Args:
            weaviate_client (Any): The weaviate client.
            embedder (Any): The embedder.
            llm (Any): The llm.
            repository (KnowledgeRepository | None): The repository (optional, default None).
            collection_registry (Any | None): The collection registry (optional, default None).
            collection_name (str): The collection name (optional, default COLLECTION_NAME).
        """
        self.client = weaviate_client
        self.embedder = embedder
        self.llm = llm
        self.repository = repository
        self.collection_registry = collection_registry
        self.collection_name = collection_name
        self._embedding_providers: dict[str, Any] = {}

    async def __call__(self, request: RetrievalQuery, *, llm: Any | None = None) -> RetrievalResult:
        """Implement the ``__call__`` protocol.

        Args:
            request (RetrievalQuery): Incoming FastAPI request.
            llm (Any | None): The llm (optional, default None).

        Returns:
            RetrievalResult: The result.
        """
        return await self.retrieve(
            request,
            owner_scope_id=request.filters.session_id,
            llm=llm,
        )

    async def retrieve(
        self,
        request: RetrievalQuery,
        *,
        owner_scope_id: str,
        llm: Any | None = None,
    ) -> RetrievalResult:
        """Retrieve the result.

        Args:
            request (RetrievalQuery): Incoming FastAPI request.
            owner_scope_id (str): The owner scope id.
            llm (Any | None): The llm (optional, default None).

        Returns:
            RetrievalResult: The result.
        """
        request_id = new_resource_id("retrieval_request")
        failed_started = time.perf_counter()
        try:
            return await self._retrieve_impl(
                request,
                owner_scope_id=owner_scope_id,
                llm=llm,
                request_id=request_id,
            )
        except Exception as exc:
            RAG_RETRIEVAL_REQUESTS.labels(
                strategy=request.strategy, status="failed"
            ).inc()
            await self._record_failed_trace(
                request=request,
                request_id=request_id,
                owner_scope_id=owner_scope_id,
                error=exc,
                total_ms=(time.perf_counter() - failed_started) * 1000,
            )
            raise

    async def _record_failed_trace(
        self,
        *,
        request: RetrievalQuery,
        request_id: str,
        owner_scope_id: str,
        error: Exception,
        total_ms: float,
    ) -> None:
        """Record the failed trace.

        Args:
            request (RetrievalQuery): Incoming FastAPI request.
            request_id (str): The request id.
            owner_scope_id (str): The owner scope id.
            error (Exception): Error value or message.
            total_ms (float): The total ms.
        """
        if self.repository is None or request.filters.session_id != owner_scope_id:
            return
        try:
            collection = await self.repository.get_collection(
                owner_scope_id, request.filters.collection_id
            )
            index_id = request.index_id or collection.active_index_id
            if not index_id:
                return
            index = await self.repository.get_index(owner_scope_id, index_id)
            await self.repository.save_trace(
                RetrievalTrace(
                    retrieval_request_id=request_id,
                    rag_agent_id=request.rag_agent_id,
                    retrieval_profile_id=request.retrieval_profile_id or "legacy_inline",
                    retrieval_profile_version=request.retrieval_profile_version or 1,
                    collection_id=collection.collection_id,
                    resolved_index_id=index.index_id,
                    parser_profile_id=index.parser_profile_id,
                    parser_profile_version=index.parser_profile_version,
                    chunking_profile_id=index.chunking_profile_id,
                    chunking_profile_version=index.chunking_profile_version,
                    embedding_profile_id=index.embedding_profile_id,
                    embedding_profile_version=index.embedding_profile_version,
                    original_query=request.query,
                    user_filters=request.filters.user_filter_dump(),
                    timings_ms={"total_ms": total_ms},
                    status="failed",
                    error=f"{type(error).__name__}: {error}"[:1000],
                    expires_at=datetime.now(timezone.utc)
                    + timedelta(days=settings.retrieval_trace_retention_days),
                    workspace_id=collection.workspace_id,
                    owner_scope_id=owner_scope_id,
                )
            )
        except Exception:
            # A tracing failure must never replace the original retrieval error.
            return

    async def _retrieve_impl(
        self,
        request: RetrievalQuery,
        *,
        owner_scope_id: str,
        request_id: str,
        llm: Any | None = None,
    ) -> RetrievalResult:
        """Retrieve the impl.

        Args:
            request (RetrievalQuery): Incoming FastAPI request.
            owner_scope_id (str): The owner scope id.
            request_id (str): The request id.
            llm (Any | None): The llm (optional, default None).

        Returns:
            RetrievalResult: The impl.
        """
        if request.filters.session_id != owner_scope_id:
            raise RetrievalAuthorizationError("retrieval security scope cannot be overridden")
        llm = llm or self.llm
        started = time.perf_counter()
        stages: list[RetrievalStage] = []
        timings: dict[str, float] = {}
        collection = None
        index = None
        logical_collection_id = request.filters.collection_id
        physical_collection_name = self.collection_name
        physical_index_id: str | None = request.index_id
        exclude_parent_chunks = False
        active_embedder = self.embedder

        normalized = " ".join(request.query.split())
        stages.append(
            RetrievalStage(
                name="normalization",
                details={"changed": normalized != request.query, "character_count": len(normalized)},
            )
        )
        request = request.model_copy(update={"query": normalized})

        if self.repository is not None:
            try:
                collection = await self.repository.get_collection(owner_scope_id, request.filters.collection_id)
            except ResourceNotFoundError:
                if request.retrieval_profile_id or request.index_id:
                    raise
                collection = None
            if collection is not None:
                if request.retrieval_profile_id:
                    profile = await self.repository.get_profile(
                        owner_scope_id,
                        request.retrieval_profile_id,
                        request.retrieval_profile_version,
                        ProfileType.RETRIEVAL,
                    )
                    config = RetrievalProfileConfig.model_validate(profile.config)
                    request = request.model_copy(
                        update={
                            "strategy": config.strategy,
                            "top_k_candidates": config.candidate_count,
                            "top_n_final": config.final_count,
                            "alpha": config.alpha,
                            "fusion_strategy": config.fusion_strategy,
                            "rerank": config.reranking_enabled,
                            "compress": config.compression_enabled,
                            "query_transform": config.query_transform,
                            "context_expansion": config.context_expansion,
                            "retrieval_profile_version": profile.version,
                        }
                    )
                if request.index_id:
                    index = await self.repository.get_index(owner_scope_id, request.index_id)
                elif collection.active_index_id:
                    index = await self.repository.get_index(owner_scope_id, collection.active_index_id)
                else:
                    raise RetrievalCompatibilityError("collection has no active index")
                if index.collection_id != collection.collection_id:
                    raise RetrievalCompatibilityError("resolved index does not belong to collection")
                physical_collection_name = index.physical_collection
                physical_collection_id = index.physical_collection_id or collection.collection_id
                physical_index_id = index.physical_index_id
                chunking_profile = await self.repository.get_profile(
                    owner_scope_id,
                    index.chunking_profile_id,
                    index.chunking_profile_version,
                    ProfileType.CHUNKING,
                )
                exclude_parent_chunks = chunking_profile.strategy == "parent_child"
                embedding_profile = await self.repository.get_profile(
                    owner_scope_id,
                    index.embedding_profile_id,
                    index.embedding_profile_version,
                    ProfileType.EMBEDDING,
                )
                expected_fingerprint = hashlib.sha256(
                    f"{embedding_profile.config.get('provider')}:{embedding_profile.config.get('model')}:{embedding_profile.config.get('dimensions')}".encode()
                ).hexdigest()[:12]
                if expected_fingerprint != index.embedding_fingerprint:
                    raise RetrievalCompatibilityError(
                        "embedding profile is incompatible with resolved index"
                    )
                active_embedder = self._embedding_providers.get(expected_fingerprint)
                if active_embedder is None:
                    active_embedder = Embedder(
                        EmbedderConfig(
                            model=str(embedding_profile.config.get("model")),
                            dimensions=int(embedding_profile.config.get("dimensions")),
                            batch_size=int(embedding_profile.config.get("batch_size", 64)),
                        )
                    )
                    self._embedding_providers[expected_fingerprint] = active_embedder
                request = request.model_copy(
                    update={
                        "index_id": index.index_id,
                        "filters": request.filters.model_copy(
                            update={"collection_id": physical_collection_id}
                        ),
                    }
                )
                validate_metadata_filters(request.filters.metadata, collection.metadata_schema)
                required_chunking = {
                    "parent": "parent_child",
                    "sentence_window": "sentence_window",
                    "contextual": "contextual",
                }.get(request.context_expansion)
                if required_chunking and chunking_profile.strategy != required_chunking:
                    raise RetrievalCompatibilityError(
                        f"{request.context_expansion} expansion requires "
                        f"{required_chunking} chunking"
                    )
        elif self.collection_registry is not None and request.filters.doc_types:
            cfg = await self.collection_registry.get(
                request.filters.collection_id, owner_scope_id=owner_scope_id
            )
            cfg.validate_doc_types(request.filters.doc_types)

        transform_started = time.perf_counter()
        semantic_query, transformed_queries, generated_filters = await transform_query(
            request,
            llm=llm,
            metadata_schema=collection.metadata_schema if collection else {},
        )
        if generated_filters is not None:
            existing = request.filters.metadata
            # A transform may only narrow an explicit caller filter. Preserve
            # the caller's internal OR semantics, then AND the generated group.
            merged = generated_filters if existing is None else MetadataFilterGroup(
                logic="and", groups=[existing, generated_filters]
            )
            request = request.model_copy(
                update={"filters": request.filters.model_copy(update={"metadata": merged})}
            )
        transform_ms = (time.perf_counter() - transform_started) * 1000
        timings["query_transformation_ms"] = transform_ms
        stages.append(
            RetrievalStage(
                name="query_transformation",
                duration_ms=transform_ms,
                input_count=1,
                output_count=len(transformed_queries),
                details={"strategy": request.query_transform, "queries": transformed_queries},
            )
        )
        stages.append(
            RetrievalStage(
                name="filtering",
                details={
                    "security": {"owner_scope_pinned": True, "collection_pinned": True, "index_pinned": bool(index)},
                    "metadata": request.filters.user_filter_dump(),
                },
            )
        )

        retrieval_started = time.perf_counter()
        per_query_results: list[list[RetrievedChunk]] = []
        dense_for_trace: list[RetrievedChunk] = []
        sparse_for_trace: list[RetrievedChunk] = []
        for query in transformed_queries:
            if request.strategy == "dense":
                result, _ = await dense_search(
                    client=self.client,
                    collection_name=physical_collection_name,
                    embedder=active_embedder,
                    query=query,
                    filters=request.filters,
                    index_id=physical_index_id,
                    top_k=request.top_k_candidates,
                    exclude_parent_chunks=exclude_parent_chunks,
                )
            elif request.strategy == "sparse":
                result, _ = await sparse_search(
                    client=self.client,
                    collection_name=physical_collection_name,
                    query=query,
                    filters=request.filters,
                    index_id=physical_index_id,
                    top_k=request.top_k_candidates,
                    exclude_parent_chunks=exclude_parent_chunks,
                )
            elif request.fusion_strategy == "rrf":
                dense, sparse = await asyncio.gather(
                    dense_search(
                        client=self.client, collection_name=physical_collection_name,
                        embedder=active_embedder, query=query, filters=request.filters,
                        index_id=physical_index_id, top_k=request.top_k_candidates,
                        exclude_parent_chunks=exclude_parent_chunks,
                    ),
                    sparse_search(
                        client=self.client, collection_name=physical_collection_name,
                        query=query, filters=request.filters, index_id=physical_index_id,
                        top_k=request.top_k_candidates,
                        exclude_parent_chunks=exclude_parent_chunks,
                    ),
                )
                dense_for_trace.extend(dense[0])
                sparse_for_trace.extend(sparse[0])
                result = reciprocal_rank_fusion(
                    [dense[0], sparse[0]], limit=request.top_k_candidates
                )
            else:
                hybrid_call = native_hybrid_search(
                    client=self.client, collection_name=physical_collection_name,
                    embedder=active_embedder, query=query, filters=request.filters,
                    index_id=physical_index_id, top_k=request.top_k_candidates,
                    alpha=request.alpha,
                    exclude_parent_chunks=exclude_parent_chunks,
                )
                if request.diagnostic_components:
                    hybrid, dense, sparse = await asyncio.gather(
                        hybrid_call,
                        dense_search(
                            client=self.client, collection_name=physical_collection_name,
                            embedder=active_embedder, query=query, filters=request.filters,
                            index_id=physical_index_id, top_k=request.top_k_candidates,
                            exclude_parent_chunks=exclude_parent_chunks,
                        ),
                        sparse_search(
                            client=self.client, collection_name=physical_collection_name,
                            query=query, filters=request.filters, index_id=physical_index_id,
                            top_k=request.top_k_candidates,
                            exclude_parent_chunks=exclude_parent_chunks,
                        ),
                    )
                    result = hybrid[0]
                    dense_for_trace.extend(dense[0])
                    sparse_for_trace.extend(sparse[0])
                    dense_scores = {item.chunk_id: item.dense_score for item in dense[0]}
                    sparse_scores = {item.chunk_id: item.sparse_score for item in sparse[0]}
                    for item in result:
                        item.dense_score = dense_scores.get(item.chunk_id)
                        item.sparse_score = sparse_scores.get(item.chunk_id)
                else:
                    result = (await hybrid_call)[0]
            per_query_results.append(result)
        if len(per_query_results) > 1:
            candidates = reciprocal_rank_fusion(
                per_query_results, limit=request.top_k_candidates
            )
        else:
            candidates = per_query_results[0] if per_query_results else []
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
        timings["retrieval_ms"] = retrieval_ms
        stages.append(
            RetrievalStage(
                name="retrieval",
                duration_ms=retrieval_ms,
                output_count=len(candidates),
                details={
                    "strategy": request.strategy,
                    "dense": [_safe_candidate(c) for c in dense_for_trace],
                    "sparse": [_safe_candidate(c) for c in sparse_for_trace],
                },
            )
        )
        stages.append(
            RetrievalStage(
                name="fusion",
                output_count=len(candidates),
                details={
                    "strategy": request.fusion_strategy,
                    "results": [_safe_candidate(c) for c in candidates],
                },
            )
        )
        candidates_snapshot = [chunk.model_copy(deep=True) for chunk in candidates]
        before_dedup = len(candidates)
        candidates = deduplicate(candidates)
        stages.append(
            RetrievalStage(name="deduplication", input_count=before_dedup, output_count=len(candidates))
        )

        if request.rerank and candidates:
            kept, rerank_ms = await rerank(
                query=semantic_query,
                candidates=candidates,
                top_n=request.top_n_final,
                llm=llm,
                model=settings.retrieval_reranker_model,
            )
            timings["rerank_ms"] = rerank_ms
        else:
            kept = candidates[: request.top_n_final]
            rerank_ms = 0.0
        stages.append(
            RetrievalStage(
                name="reranking",
                duration_ms=rerank_ms,
                input_count=len(candidates),
                output_count=len(kept),
                details={"enabled": request.rerank, "results": [_safe_candidate(c) for c in kept]},
            )
        )

        if request.compress and kept:
            kept, compression_ms = await compress_chunks(
                query=semantic_query,
                chunks=kept,
                llm=llm,
                model=settings.retrieval_compressor_model,
            )
            timings["compress_ms"] = compression_ms

        expansion_started = time.perf_counter()
        kept = await expand_context(
            kept,
            strategy=request.context_expansion,
            client=self.client,
            collection_name=physical_collection_name,
            filters=request.filters,
            index_id=physical_index_id,
        )
        expansion_ms = (time.perf_counter() - expansion_started) * 1000
        timings["context_expansion_ms"] = expansion_ms
        stages.append(
            RetrievalStage(
                name="context_expansion",
                duration_ms=expansion_ms,
                output_count=len(kept),
                details={"strategy": request.context_expansion},
            )
        )
        context, token_count = assemble_context(kept)
        stages.append(
            RetrievalStage(
                name="context_assembly",
                input_count=len(kept),
                output_count=len(kept),
                details={"token_count": token_count},
            )
        )
        timings["total_ms"] = (time.perf_counter() - started) * 1000
        RAG_RETRIEVAL_REQUESTS.labels(strategy=request.strategy, status="success").inc()
        RAG_RETRIEVAL_LATENCY.labels(strategy=request.strategy).observe(
            timings["total_ms"] / 1000.0
        )
        RAG_CANDIDATES.labels(strategy=request.strategy).inc(len(candidates_snapshot))
        RAG_CONTEXT_CHUNKS.labels(strategy=request.strategy).inc(len(kept))

        result = RetrievalResult(
            query=request.query,
            rewritten_query=semantic_query if semantic_query != request.query else None,
            transformed_queries=transformed_queries,
            chunks=kept,
            candidates=candidates_snapshot,
            filters_applied=request.filters.model_copy(
                update={"collection_id": logical_collection_id}
            ),
            timings_ms=timings,
            retrieval_request_id=request_id,
            collection_id=logical_collection_id,
            resolved_index_id=request.index_id,
            retrieval_profile_id=request.retrieval_profile_id,
            retrieval_profile_version=request.retrieval_profile_version,
            stages=stages,
            final_context=context,
            context_token_count=token_count,
            strategy=request.strategy,
            resolved_resources=(
                {
                    "collection_id": logical_collection_id,
                    "index_id": index.index_id,
                    "parser_profile_id": index.parser_profile_id,
                    "parser_profile_version": index.parser_profile_version,
                    "chunking_profile_id": index.chunking_profile_id,
                    "chunking_profile_version": index.chunking_profile_version,
                    "embedding_profile_id": index.embedding_profile_id,
                    "embedding_profile_version": index.embedding_profile_version,
                    "retrieval_profile_id": request.retrieval_profile_id,
                    "retrieval_profile_version": request.retrieval_profile_version,
                }
                if index is not None
                else {}
            ),
        )
        if self.repository is not None and index is not None:
            await self.repository.save_trace(
                RetrievalTrace(
                    retrieval_request_id=request_id,
                    rag_agent_id=request.rag_agent_id,
                    retrieval_profile_id=request.retrieval_profile_id or "legacy_inline",
                    retrieval_profile_version=request.retrieval_profile_version or 1,
                    collection_id=logical_collection_id,
                    resolved_index_id=index.index_id,
                    parser_profile_id=index.parser_profile_id,
                    parser_profile_version=index.parser_profile_version,
                    chunking_profile_id=index.chunking_profile_id,
                    chunking_profile_version=index.chunking_profile_version,
                    embedding_profile_id=index.embedding_profile_id,
                    embedding_profile_version=index.embedding_profile_version,
                    original_query=request.query,
                    transformed_queries=transformed_queries,
                    security_filters={
                        "owner_scope_id": owner_scope_id,
                        "collection_id": logical_collection_id,
                        "physical_collection_id": request.filters.collection_id,
                        "physical_index_id": physical_index_id,
                    },
                    user_filters=request.filters.user_filter_dump(),
                    candidates=[_safe_candidate(chunk) for chunk in candidates_snapshot],
                    selected_context=[_safe_candidate(chunk) for chunk in kept],
                    final_context=context,
                    context_token_count=token_count,
                    timings_ms=timings,
                    expires_at=datetime.now(timezone.utc)
                    + timedelta(days=settings.retrieval_trace_retention_days),
                    workspace_id=collection.workspace_id,
                    owner_scope_id=owner_scope_id,
                )
            )
        return result

    async def compare(
        self,
        requests: list[RetrievalQuery],
        *,
        owner_scope_id: str,
        llm: Any | None = None,
    ) -> list[RetrievalResult]:
        """Compare the result.

        Args:
            requests (list[RetrievalQuery]): The requests.
            owner_scope_id (str): The owner scope id.
            llm (Any | None): The llm (optional, default None).

        Returns:
            list[RetrievalResult]: The result.
        """
        if not 2 <= len(requests) <= 4:
            raise ValueError("comparison requires two to four retrieval configurations")
        results = []
        for request in requests:
            results.append(await self.retrieve(request, owner_scope_id=owner_scope_id, llm=llm))
        return results
