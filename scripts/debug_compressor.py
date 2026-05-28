"""Debug the compressor in isolation.

Take one chunk that the reranker scored highly, call the compressor on
it directly, and print exactly what the LLM returns. This isolates
compressor behavior from the rest of the pipeline.
"""
from __future__ import annotations

import asyncio
import weaviate

from app.config import settings
from app.ingestion.embedder import Embedder
from app.llm import get_gateway
from app.retrieval.compressor import _compress_one
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.models import RetrievalFilters


async def main():
    weaviate_client = weaviate.use_async_with_local(host="weaviate", port=8080)
    await weaviate_client.connect()
    embedder = Embedder()
    gw, _ = get_gateway("claude-haiku-4-5")

    query = "What is the technical approach and methodology described in the proposal?"
    filters = RetrievalFilters(
        session_id="default",
        doc_types=["proposal"],
    )

    try:
        # Get just one candidate from hybrid search
        candidates, _ = await hybrid_search(
            client=weaviate_client,
            embedder=embedder,
            collection_name=settings.weaviate_collection,
            query=query,
            filters=filters,
            top_k=1,
            alpha=0.5,
        )

        if not candidates:
            print("No candidates returned.")
            return

        chunk = candidates[0]
        print(f"Chunk hybrid_score: {chunk.hybrid_score}")
        print(f"Chunk source: {chunk.doc_title}")
        print(f"Chunk text (first 800 chars):\n{chunk.text[:800]}")
        print("\n" + "=" * 70 + "\n")

        # Call the compressor directly
        compressed = await _compress_one(
            query=query,
            chunk=chunk,
            llm=gw,
            model=settings.retrieval_compressor_model,
        )

        print(f"Compressed result: {compressed.compressed_text!r}")
        print(f"Length: {len(compressed.compressed_text or '')}")

    finally:
        await weaviate_client.close()


if __name__ == "__main__":
    asyncio.run(main())