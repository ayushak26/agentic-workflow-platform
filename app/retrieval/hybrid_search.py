"""Stage 2 of the retrieval pipeline: hybrid search with metadata pre-filter.

Embeds the query via the same Embedder used at ingestion (text-embedding-
3-small) and passes the vector explicitly. The collection is configured
with vectorizer=none — embedding is an application concern, not a DB one.
"""
from __future__ import annotations

import time
import structlog
import weaviate
import asyncio
from weaviate.classes.query import Filter, HybridFusion

from app.ingestion.embedder import Embedder
from app.retrieval.models import RetrievalFilters, RetrievedChunk

log = structlog.get_logger(__name__)


def _build_where_filter(f: RetrievalFilters) -> Filter:
    """Translate filters into a Weaviate Filter expression.
    Always pins session_id — this is a security boundary."""
    clauses = [Filter.by_property("session_id").equal(f.session_id)]
    if f.industry:
        clauses.append(Filter.by_property("industry").equal(f.industry))
    if f.doc_types:
        clauses.append(Filter.by_property("doc_type").contains_any(f.doc_types))
    if f.collection_ids:                                                            
        clauses.append(Filter.by_property("collection_id").contains_any(f.collection_ids))    
    if f.date_after:
        clauses.append(Filter.by_property("ingested_at").greater_than(f.date_after))
    if f.date_before:
        clauses.append(Filter.by_property("ingested_at").less_than(f.date_before))
    return Filter.all_of(clauses)


async def hybrid_search(
    client: weaviate.WeaviateAsyncClient,
    embedder: Embedder,
    collection_name: str,
    query: str,
    filters: RetrievalFilters,
    top_k: int,
    alpha: float,
) -> tuple[list[RetrievedChunk], float]:
    """Hybrid BM25 + vector search with metadata pre-filter.

    The collection is configured with vectorizer=none, so we embed the
    query here using the same Embedder Phase 2 uses for chunks. The
    string `query` powers BM25; the embedded `vector` powers vector
    search; Weaviate fuses both per `alpha`.
    """
    started = time.perf_counter()

    # 1. Embed the query — same model as ingestion, so both vectors live
    # in the same semantic space.
    vectors = await embedder.embed([query])
    query_vector = vectors[0]

    where = _build_where_filter(filters)
    collection = client.collections.get(collection_name)

    # 2. Hybrid call with both halves provided explicitly.
    response = await asyncio.to_thread(
    collection.query.hybrid, 
        query=query,
        vector=query_vector,                          # ← the fix
        alpha=alpha,
        limit=top_k,
        filters=where,
        fusion_type=HybridFusion.RELATIVE_SCORE,
        return_metadata=["score", "explain_score"],
    )
    latency_ms = (time.perf_counter() - started) * 1000

    chunks = [
        RetrievedChunk(
            chunk_id=str(obj.properties.get("chunk_id", obj.uuid)),
            doc_id=obj.properties.get("source_path", ""),
            doc_title=obj.properties.get("source_path", "").split("/")[-1],
            doc_type=obj.properties["doc_type"],
            text=obj.properties["text"],
            compressed_text=None,
            metadata={k: v for k, v in obj.properties.items()
                      if k not in {"text", "doc_type"}},
            hybrid_score=obj.metadata.score,
            rerank_score=None,
            rerank_reason=None,
        )
        for obj in response.objects
    ]
    log.info(
        "hybrid_search.complete",
        candidates=len(chunks), alpha=alpha,
        latency_ms=latency_ms, session=filters.session_id,
    )
    return chunks, latency_ms