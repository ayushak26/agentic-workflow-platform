"""The retrieval pipeline orchestrator.

Public entry point for the whole app.retrieval module. Stitches together
the four stages — query understanding, hybrid search, rerank, compression —
and returns a typed RetrievalResult.

This file owns no retrieval logic. If you're adding an 'if' here that isn't
a stage toggle, it belongs in one of the stage files.
"""
from __future__ import annotations

import time

import structlog
import weaviate

from app.config import settings
from app.llm import LLMGateway
from app.retrieval.compressor import compress_chunks
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.models import RetrievalQuery, RetrievalResult, RetrievedChunk
from app.retrieval.weaviate_client import COLLECTION_NAME
from app.retrieval.query_understanding import rewrite_query
from app.ingestion.embedder import Embedder
from app.retrieval.reranker import rerank

log = structlog.get_logger(__name__)


async def retrieve(
    q: RetrievalQuery,
    *,
    weaviate_client: weaviate.WeaviateAsyncClient,
    llm: LLMGateway,
    embedder: Embedder,
    collection_registry=None, 
) -> RetrievalResult:
    """Run the four-stage retrieval pipeline.

    Stages:
      1. Query understanding (optional, q.rewrite_query)
      2. Hybrid search with metadata pre-filter — always runs
      3. LLM rerank (optional, q.rerank)
      4. Contextual compression (optional, q.compress)

    Returns a RetrievalResult with per-stage timings for observability.
    """
    pipeline_started = time.perf_counter()
    timings_ms: dict[str, float] = {}

    log.info(
        "retrieve.start",
        query=q.query,
        session=q.filters.session_id,
        top_k=q.top_k_candidates,
        top_n=q.top_n_final,
        alpha=q.alpha,
        stages={"rewrite": q.rewrite_query,
                "rerank": q.rerank,
                "compress": q.compress},
    )

    # ---- Stage 0 — vocabulary + collection validation (cheapest, runs first) ----
    # collection_id does double duty: it's AND-ed into the Weaviate filter
    # (corpus isolation, in hybrid_search) AND the key that loads the controlled
    # vocabulary here. Validating before Stage 1 means a bad doc_type or an
    # unregistered collection fails before any LLM/embedding spend.
    if collection_registry is not None and q.filters.doc_types:
        cfg = await collection_registry.get(q.filters.collection_id)  # KeyError if unregistered
        cfg.validate_doc_types(q.filters.doc_types)                    # ValueError

    # ---- Stage 1 — query understanding -------------------------------
    rewritten: str | None = None
    if q.rewrite_query:
        t0 = time.perf_counter()
        rewritten = await rewrite_query(q.query, llm=llm)
        timings_ms["query_understanding_ms"] = (time.perf_counter() - t0) * 1000

    effective_query = rewritten or q.query

    # ---- Stage 2 — hybrid search with metadata pre-filter ------------
    candidates, t = await hybrid_search(
        client=weaviate_client,
        collection_name=COLLECTION_NAME,
        embedder=embedder,
        query=effective_query,
        filters=q.filters,
        top_k=q.top_k_candidates,
        alpha=q.alpha,
    )
    timings_ms["hybrid_search_ms"] = t

    if not candidates:
        log.warning("retrieve.no_candidates",
                    query=q.query, filters=q.filters.model_dump())
        return RetrievalResult(
            query=q.query,
            rewritten_query=rewritten,
            chunks=[],
            filters_applied=q.filters,
            timings_ms=timings_ms,
        )

    # ---- Stage 3 — rerank --------------------------------------------
    if q.rerank:
        kept, t = await rerank(
            query=effective_query,
            candidates=candidates,
            top_n=q.top_n_final,
            llm=llm,
            model=settings.retrieval_reranker_model,
        )
        timings_ms["rerank_ms"] = t
    else:
        # No rerank — take top_n by raw hybrid score.
        kept = sorted(candidates, key=lambda c: c.hybrid_score, reverse=True)
        kept = kept[: q.top_n_final]

    # ---- Stage 4 — compression ---------------------------------------
    if q.compress and kept:
        kept, t = await compress_chunks(
            query=effective_query,
            chunks=kept,
            llm=llm,
            model=settings.retrieval_compressor_model,
        )
        timings_ms["compress_ms"] = t

    timings_ms["total_ms"] = (time.perf_counter() - pipeline_started) * 1000

    log.info(
        "retrieve.complete",
        query=q.query,
        chunks_returned=len(kept),
        timings_ms=timings_ms,
    )

    return RetrievalResult(
        query=q.query,
        rewritten_query=rewritten,
        chunks=kept,
        filters_applied=q.filters,
        timings_ms=timings_ms,
    )