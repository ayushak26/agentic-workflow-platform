"""Stage 3 of the retrieval pipeline: LLM-as-judge reranking.

Takes the ~25 candidates from hybrid search, scores them all in a single
LLM call, and returns the top N sorted by score. Each kept chunk gets a
one-sentence reason that surfaces in the Cockpit retrievals panel.

Why an LLM reranker and not a cross-encoder:
- No domain fine-tuning needed for consultancy content.
- Per-chunk reasoning trace is surfaceable in the UI.
- Bounded candidate count (25) keeps cost and latency manageable.
"""
from __future__ import annotations

import time
from typing import Optional

import structlog
from pydantic import BaseModel, Field

from app.llm import LLMGateway
from app.retrieval.models import RetrievedChunk

log = structlog.get_logger(__name__)


# Per-chunk text budget for the rerank prompt. The full chunk text is
# preserved on the model; we only truncate the *view* the reranker gets.
_RERANK_CHUNK_CHAR_BUDGET = 1000   # roughly 250 tokens


# ---- Structured output schema -------------------------------------------

class _RerankScore(BaseModel):
    """One score for one chunk. Returned as part of a JSON array."""
    chunk_id: str
    score: float = Field(..., ge=0.0, le=10.0)
    reason: str = Field(..., min_length=1, max_length=300)


class _RerankResponse(BaseModel):
    """Whole response: a list of scores, one per input chunk."""
    scores: list[_RerankScore]


# ---- Prompts ------------------------------------------------------------

_RERANK_SYSTEM = """You are a retrieval relevance judge. You will be given
a question and a numbered list of candidate passages. For each candidate,
return a relevance score from 0 to 10 and a one-sentence reason.

Scoring guide:
- 9-10: directly answers the question with specific facts, names, numbers
- 6-8:  clearly on-topic and provides useful context, but not the full answer
- 3-5:  tangentially related, same domain but different sub-topic
- 0-2:  off-topic or unrelated

Rules:
- Score every candidate. Do not skip any.
- Use the exact chunk_id from the input.
- Reasons must be one sentence, concrete, and reference the candidate's content.
- Return scores in the same order as the input.
"""


def _format_candidates(candidates: list[RetrievedChunk]) -> str:
    """Build the numbered candidate block for the user prompt."""
    parts = []
    for i, c in enumerate(candidates, start=1):
        snippet = c.text[:_RERANK_CHUNK_CHAR_BUDGET]
        if len(c.text) > _RERANK_CHUNK_CHAR_BUDGET:
            snippet += " […]"
        parts.append(
            f"[{i}] chunk_id={c.chunk_id} | doc_type={c.doc_type} | "
            f"title={c.doc_title}\n{snippet}"
        )
    return "\n\n".join(parts)


# ---- Public API ---------------------------------------------------------

async def rerank(
    query: str,
    candidates: list[RetrievedChunk],
    top_n: int,
    llm: LLMGateway,
    model: str = "gpt-5",
) -> tuple[list[RetrievedChunk], float]:
    """Score all candidates in one LLM call, return top_n sorted by score.

    Sets rerank_score and rerank_reason on each returned chunk. Returns
    (kept_chunks, latency_ms).

    If the LLM fails to score every candidate (network blip, malformed
    output), we fall back to the original hybrid_score ordering and log
    a warning — the pipeline must never crash on a reranker failure.
    """
    started = time.perf_counter()

    if not candidates:
        return [], 0.0

    user_prompt = (
        f"Question:\n{query}\n\n"
        f"Candidates:\n{_format_candidates(candidates)}\n\n"
        f"Return a JSON object with a 'scores' array containing one entry "
        f"per candidate."
    )

    try:
        response: _RerankResponse = await llm.complete_structured(
            model=model,
            system=_RERANK_SYSTEM,
            user=user_prompt,
            response_model=_RerankResponse,
            temperature=0.0,
            max_tokens=8000,
        )
    except Exception as exc:
        log.warning("reranker.llm_failed", error=str(exc),
                    fallback="hybrid_score_order")
        kept = sorted(candidates, key=lambda c: c.hybrid_score, reverse=True)[:top_n]
        return kept, (time.perf_counter() - started) * 1000

    # Index scores by chunk_id for a robust merge. The model is asked to
    # preserve order, but we don't trust order — we trust the id.
    score_by_id = {s.chunk_id: s for s in response.scores}

    scored: list[RetrievedChunk] = []
    missing = 0
    for c in candidates:
        s = score_by_id.get(c.chunk_id)
        if s is None:
            missing += 1
            # Conservative: keep the chunk in the pool with a low score
            # rather than dropping it silently.
            c.rerank_score = 0.0
            c.rerank_reason = "Reranker skipped this candidate."
        else:
            c.rerank_score = s.score
            c.rerank_reason = s.reason
        scored.append(c)

    if missing:
        log.warning("reranker.missing_scores",
                    missing=missing, total=len(candidates))

    scored.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
    kept = scored[:top_n]

    latency_ms = (time.perf_counter() - started) * 1000
    log.info(
        "reranker.complete",
        input_candidates=len(candidates),
        kept=len(kept),
        top_score=kept[0].rerank_score if kept else None,
        latency_ms=latency_ms,
    )
    return kept, latency_ms