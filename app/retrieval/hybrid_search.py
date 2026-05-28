import time
import structlog
import weaviate
from weaviate.classes.query import Filter, HybridFusion

from app.retrieval.models import RetrievalFilters, RetrievedChunk

log = structlog.get_logger(__name__)

def _build_where_filter(f: RetrievalFilters) -> Filter | None:
    """Translate our Pydantic filters into a Weaviate Filter expression.
    Always pins session_id — this is a security boundary, not a convenience."""
    clauses = [Filter.by_property("session_id").equal(f.session_id)]
    if f.industry:
        clauses.append(Filter.by_property("industry").equal(f.industry))
    if f.doc_types:
        clauses.append(Filter.by_property("doc_type").contains_any(f.doc_types))
    if f.date_after:
        clauses.append(Filter.by_property("doc_date").greater_than(f.date_after))
    if f.date_before:
        clauses.append(Filter.by_property("doc_date").less_than(f.date_before))
    return Filter.all_of(clauses)

async def hybrid_search(
    client: weaviate.WeaviateAsyncClient,
    collection_name: str,
    query: str,
    filters: RetrievalFilters,
    top_k: int,
    alpha: float,
) -> tuple[list[RetrievedChunk], float]:
    """Hybrid BM25 + vector search with metadata pre-filter.
    Returns (chunks, latency_ms)."""
    started = time.perf_counter()
    where = _build_where_filter(filters)

    collection = client.collections.get(collection_name)
    response = await collection.query.hybrid(
        query=query,
        alpha=alpha,                                  # 0=BM25, 1=vector
        limit=top_k,
        filters=where,                                # pre-filter
        fusion_type=HybridFusion.RELATIVE_SCORE,      # both signals normalized
        return_metadata=["score", "explain_score"],   # for debugging + logs
    )
    latency_ms = (time.perf_counter() - started) * 1000

    chunks = [
        RetrievedChunk(
            chunk_id=str(obj.uuid),
            doc_id=obj.properties["doc_id"],
            doc_title=obj.properties["doc_title"],
            doc_type=obj.properties["doc_type"],
            text=obj.properties["text"],
            compressed_text=None,
            metadata={k: v for k, v in obj.properties.items()
                      if k not in {"text", "doc_id", "doc_title", "doc_type"}},
            hybrid_score=obj.metadata.score,
            rerank_score=None,
            rerank_reason=None,
        )
        for obj in response.objects
    ]
    log.info("hybrid_search.complete",
             candidates=len(chunks), alpha=alpha,
             latency_ms=latency_ms, session=filters.session_id)
    return chunks, latency_ms