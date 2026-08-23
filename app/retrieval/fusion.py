"""Reciprocal Rank Fusion for independently executed retrieval result sets."""
from __future__ import annotations

from app.retrieval.models import RetrievedChunk

RRF_K = 60  # standard RRF damping constant


def reciprocal_rank_fusion(
    result_sets: list[list[RetrievedChunk]], *, limit: int
) -> list[RetrievedChunk]:
    """Fuse independently-ranked result sets (dense+sparse, or per-query
    multi-query results) into one ranking via Reciprocal Rank Fusion.

    Each chunk's fused score is ``sum(1 / (RRF_K + rank))`` across every
    result set it appears in (1-indexed rank). The best-scoring occurrence of
    each ``chunk_id`` is kept; ``fusion_score`` is overwritten with the RRF
    score so downstream stages (dedup, reranking, trace) see one number.
    """

    scores: dict[str, float] = {}
    best: dict[str, RetrievedChunk] = {}
    for result_set in result_sets:
        for rank, chunk in enumerate(result_set, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            if chunk.chunk_id not in best:
                best[chunk.chunk_id] = chunk

    ordered = sorted(best.values(), key=lambda c: scores[c.chunk_id], reverse=True)
    for position, chunk in enumerate(ordered, start=1):
        chunk.fusion_score = scores[chunk.chunk_id]
        chunk.rank = position
    return ordered[:limit]


def deduplicate(candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Drop repeat ``chunk_id``s, keeping the highest-scoring occurrence.

    Parent/child and multi-query fan-out can surface the same chunk more than
    once before this stage; order is preserved for everything else.
    """

    def score(chunk: RetrievedChunk) -> float:
        """Score the result.

        Args:
            chunk (RetrievedChunk): The chunk.

        Returns:
            float: The result.
        """
        return chunk.fusion_score or chunk.hybrid_score or 0.0

    kept: dict[str, RetrievedChunk] = {}
    order: list[str] = []
    for chunk in candidates:
        existing = kept.get(chunk.chunk_id)
        if existing is None:
            kept[chunk.chunk_id] = chunk
            order.append(chunk.chunk_id)
        elif score(chunk) > score(existing):
            kept[chunk.chunk_id] = chunk
    return [kept[chunk_id] for chunk_id in order]
