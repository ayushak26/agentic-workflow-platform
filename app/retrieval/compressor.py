"""Stage 4 of the retrieval pipeline: contextual compression.

For each reranked chunk, extract only the sentences that help answer the
query. Verbatim, in order. This shrinks the prompt the drafter sees, helps
with lost-in-the-middle, and preserves citation integrity (we never
paraphrase the source).

Pattern: LangChain LLMChainExtractor. Implemented directly on our LLM
gateway so we stay provider-agnostic.
"""
from __future__ import annotations

import asyncio
import time

import structlog

from app.llm import LLMGateway
from app.retrieval.models import RetrievedChunk

log = structlog.get_logger(__name__)


_COMPRESS_SYSTEM = """You are an extraction tool. You will be given a
question and a passage. Return only the sentences from the passage,
verbatim and in the original order, that help answer the question.

Rules:
- Do not paraphrase. Do not summarize. Copy sentences exactly.
- Preserve original punctuation and capitalization.
- If no sentence in the passage helps answer the question, return the
  single token: NONE
- Do not add any preamble, explanation, or closing remark.
"""


_COMPRESS_USER = """Question:
{query}

Passage:
{passage}

Extracted sentences:"""


async def _compress_one(
    query: str,
    chunk: RetrievedChunk,
    llm: LLMGateway,
    model: str,
) -> RetrievedChunk:
    """Compress a single chunk. Mutates and returns the same chunk."""
    response = await llm.complete(
        model=model,
        system=_COMPRESS_SYSTEM,
        user=_COMPRESS_USER.format(query=query, passage=chunk.text),
        temperature=0.0,
        max_tokens=min(len(chunk.text), 800),
    )
    extracted = response.text.strip()

    if extracted == "NONE" or not extracted:
        chunk.compressed_text = ""
        return chunk

    # Hallucination guard — extractive output can never be longer than input.
    if len(extracted) > len(chunk.text):
        log.warning(
            "compressor.expanded_output",
            chunk_id=chunk.chunk_id,
            original_len=len(chunk.text),
            output_len=len(extracted),
        )
        chunk.compressed_text = chunk.text
        return chunk

    chunk.compressed_text = extracted
    return chunk


async def compress_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    llm: LLMGateway,
    model: str = "claude-haiku-4-5",
) -> tuple[list[RetrievedChunk], float]:
    """Compress all chunks in parallel.

    Returns (kept_chunks, latency_ms). Chunks where compression returned
    NONE are dropped — they passed rerank but had no answering content.
    """
    started = time.perf_counter()

    compressed = await asyncio.gather(
        *(_compress_one(query, c, llm, model) for c in chunks)
    )
    kept = [c for c in compressed if c.compressed_text]
    latency_ms = (time.perf_counter() - started) * 1000

    log.info(
        "compressor.complete",
        input_chunks=len(chunks),
        kept_chunks=len(kept),
        dropped=len(chunks) - len(kept),
        latency_ms=latency_ms,
    )
    return kept, latency_ms