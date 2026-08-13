"""Retrieval fusion, dedup, context assembly and query-transform fallbacks.

These stages sit between the datastore and the RAG/workflow surface. They
are covered independently of a live Weaviate instance because none of them
touch the network — RRF and dedup work on already-fetched RetrievedChunk
lists, and query_transform degrades to a no-op whenever the LLM is None.
"""
from __future__ import annotations

import pytest

from app.retrieval.context import assemble_context
from app.retrieval.fusion import deduplicate, reciprocal_rank_fusion
from app.retrieval.models import RetrievalFilters, RetrievalQuery, RetrievedChunk
from app.retrieval.query_transform import transform_query


def _chunk(chunk_id: str, **overrides) -> RetrievedChunk:
    base = dict(chunk_id=chunk_id, doc_id="doc-1", doc_title="Dura 25 Manual", doc_type="manual", text=f"text for {chunk_id}")
    base.update(overrides)
    return RetrievedChunk(**base)


# ---- Reciprocal Rank Fusion -----------------------------------------------

def test_rrf_ranks_a_chunk_appearing_in_both_result_sets_above_a_single_appearance():
    dense = [_chunk("a"), _chunk("b"), _chunk("c")]
    sparse = [_chunk("b"), _chunk("a"), _chunk("d")]
    fused = reciprocal_rank_fusion([dense, sparse], limit=10)
    ids = [chunk.chunk_id for chunk in fused]
    # "a" and "b" both appear in every result set; "c"/"d" appear in only one.
    assert ids.index("a") < ids.index("c")
    assert ids.index("b") < ids.index("d")


def test_rrf_respects_the_limit():
    dense = [_chunk(f"d{i}") for i in range(20)]
    fused = reciprocal_rank_fusion([dense], limit=5)
    assert len(fused) == 5


def test_rrf_sets_fusion_score_and_rank_on_every_returned_chunk():
    fused = reciprocal_rank_fusion([[_chunk("a"), _chunk("b")]], limit=10)
    assert all(chunk.fusion_score is not None for chunk in fused)
    assert [chunk.rank for chunk in fused] == [1, 2]


# ---- Deduplication ----------------------------------------------------------

def test_deduplicate_keeps_the_highest_scoring_occurrence():
    low = _chunk("a", hybrid_score=0.2)
    high = _chunk("a", hybrid_score=0.9)
    kept = deduplicate([low, high])
    assert len(kept) == 1
    assert kept[0].hybrid_score == 0.9


def test_deduplicate_preserves_order_of_first_occurrence_for_distinct_ids():
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]
    kept = deduplicate(chunks)
    assert [c.chunk_id for c in kept] == ["a", "b", "c"]


# ---- Context assembly --------------------------------------------------------

def test_assemble_context_prefers_compressed_over_raw_text():
    chunk = _chunk("a", text="full raw chunk text", compressed_text="short compressed excerpt")
    context, token_count = assemble_context([chunk])
    assert "short compressed excerpt" in context
    assert "full raw chunk text" not in context
    assert token_count > 0


def test_assemble_context_falls_back_to_raw_text_when_nothing_else_is_set():
    chunk = _chunk("a", text="only the raw text exists")
    context, _ = assemble_context([chunk])
    assert "only the raw text exists" in context


def test_assemble_context_numbers_sources_positionally_starting_at_one():
    context, _ = assemble_context([_chunk("a"), _chunk("b")])
    assert context.startswith("[1]")
    assert "[2]" in context


# ---- Query transform: safe degradation without an LLM ------------------------

@pytest.mark.asyncio
async def test_transform_query_none_mode_is_a_pure_passthrough():
    query = RetrievalQuery(query="hello", filters=RetrievalFilters(session_id="s", collection_id="c"))
    semantic, transformed, generated_filters = await transform_query(query, llm=None, metadata_schema={})
    assert semantic == "hello"
    assert transformed == ["hello"]
    assert generated_filters is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rewrite", "multi_query", "decomposition", "hyde", "self_query"])
async def test_every_transform_mode_degrades_to_the_original_query_without_an_llm(mode):
    query = RetrievalQuery(
        query="how should Dura 25 be cleaned",
        filters=RetrievalFilters(session_id="s", collection_id="c"),
        query_transform=mode,
    )
    semantic, transformed, generated_filters = await transform_query(query, llm=None, metadata_schema={})
    assert query.query in transformed or semantic == query.query
    assert generated_filters is None
