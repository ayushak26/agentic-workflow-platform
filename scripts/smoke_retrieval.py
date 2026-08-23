"""Phase 3 smoke test — full retrieval pipeline end-to-end.

Query is deliberately mid-difficulty: it touches both proposals in the
seeded corpus (BYEPFAS = Horizon Europe RIA, FARMLOOPS = agritech). The
reranker has to genuinely rank cross-domain chunks against the same
question, which is the point of having a reranker at all.

What success looks like:
  - non-empty chunks list (3–8 results)
  - per-stage timings populated for hybrid_search, rerank, compress
  - sensible one-sentence rerank_reason per chunk
  - non-empty compressed_text per chunk (or empty if the compressor
    judged the chunk irrelevant after all — also valid)
  - total latency under 10 seconds

This script owns its own client lifecycle. Weaviate and the LLM gateway
are constructed here and closed in a finally block — no module-level
singletons, no resource leaks.
"""
from __future__ import annotations

import asyncio
import weaviate

from app.llm import get_gateway
from app.retrieval import retrieve, RetrievalQuery, RetrievalFilters
from app.ingestion.embedder import Embedder


QUERY = "What is the technical approach and methodology described in the proposal?"


async def main():
    # ---- Set up clients ----------------------------------------------
    # Async Weaviate client because our retrieval pipeline is async.
    # The factory returns a client object; we still need to call
    # connect() before using it.
    """Compute the main."""
    weaviate_client = weaviate.use_async_with_local(host="weaviate", port=8080)
    await weaviate_client.connect()
    embedder = Embedder()
    # Ask for Claude — the gateway's fallback layer will resolve it to
    # GPT via the documented map. Same gateway, same model, both
    # reranker and compressor use the architectural-default model name.
    gw, resolved_model = get_gateway("claude-haiku-4-5")
    print(f"LLM: asked for claude-haiku-4-5, resolved to {resolved_model}\n")

    # ---- Build the query --------------------------------------------
    # We deliberately do NOT filter by industry — we want the reranker
    # to see chunks from both BYEPFAS (research_innovation) and FARMLOOPS
    # (agritech). Filtering by industry='agritech' here would short-
    # circuit the test by narrowing the candidate pool to one document.
    q = RetrievalQuery(
        query=QUERY,
        filters=RetrievalFilters(
            session_id="default",     # the only session in the corpus
            doc_types=["proposal"],    # both files are proposals
            # industry intentionally omitted — let the reranker decide
        ),
        top_k_candidates=15,           # generous: 181 chunks total, 15 fits
        top_n_final=5,                 # smoke test wants visible output
        rewrite_query=True,            # stub returns query unchanged
        rerank=True,
        compress=True,
    )

    print(f"Query: {q.query}")
    print(f"Filters: session={q.filters.session_id} "
          f"doc_types={q.filters.doc_types} industry={q.filters.industry}")
    print(f"Pipeline: rewrite={q.rewrite_query} rerank={q.rerank} "
          f"compress={q.compress}")
    print()

    try:
        result = await retrieve(q, weaviate_client=weaviate_client, llm=gw, embedder=embedder)
    finally:
        await weaviate_client.close()
        if hasattr(embedder, "close"):
            close_result = embedder.close()
            if asyncio.iscoroutine(close_result):
                await close_result

    # ---- Render results --------------------------------------------
    print("=" * 70)
    print(f"Timings (ms):")
    for stage, ms in result.timings_ms.items():
        print(f"  {stage:25s}  {ms:8.1f}")
    print()

    if not result.chunks:
        print("⚠ No chunks returned. Check filters and corpus state.")
        return

    print(f"Returned {len(result.chunks)} chunks:\n")
    for i, c in enumerate(result.chunks, start=1):
        print(f"--- Result {i} ---")
        print(f"  source:        {c.doc_title}")
        print(f"  doc_type:      {c.doc_type}")
        print(f"  hybrid_score:  {c.hybrid_score:.3f}")
        print(f"  rerank_score:  {c.rerank_score}")
        print(f"  rerank_reason: {c.rerank_reason}")
        if c.compressed_text:
            preview = c.compressed_text.replace("\n", " ").strip()
            if len(preview) > 250:
                preview = preview[:250] + "..."
            print(f"  compressed:    {preview}")
        else:
            print(f"  compressed:    (empty — compressor dropped this chunk)")
        print()


if __name__ == "__main__":
    asyncio.run(main())