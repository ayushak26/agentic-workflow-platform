"""Ingestion chunking strategies, the stage registry, and the ingestion contracts."""
from __future__ import annotations

import pytest

from app.ingestion.contracts import StageRegistry, UnknownStrategyError
from app.ingestion.extractor import ExtractedDocument, ExtractedUnit
from app.ingestion.strategies import (
    ContextualChunkingStrategy,
    DEFAULT_STAGE_REGISTRY,
    FixedTokenChunkingStrategy,
    ParentChildChunkingStrategy,
    RecursiveChunkingStrategy,
    SemanticChunkingStrategy,
    SentenceWindowChunkingStrategy,
    StructureAwareChunkingStrategy,
)


def _document(text: str, *, label: str = "unit-0") -> ExtractedDocument:
    return ExtractedDocument(
        source_path="/tmp/sample.txt", source_format="txt", page_count=1,
        units=[ExtractedUnit(index=0, label=label, text=text)],
        metadata={"title": "Sample Document"},
    )


LONG_TEXT = " ".join(f"sentence number {i} about chemical compatibility topic {i % 5}." for i in range(200))


# ---- Chunk.embedding_content fallback (fixes the jobs.py/strategies.py mismatch) ----

def test_chunk_embedding_content_falls_back_to_raw_text():
    from app.ingestion.chunker import Chunk

    plain = Chunk(chunk_id="c1", text="raw text", token_count=2)
    assert plain.embedding_content == "raw text"

    enriched = Chunk(chunk_id="c2", text="raw text", token_count=2, retrieval_content="Document: X\n\nraw text")
    assert enriched.embedding_content == "Document: X\n\nraw text"


# ---- Each chunking strategy produces content ----------------------------

@pytest.mark.asyncio
async def test_recursive_chunking_produces_chunks_with_provenance_metadata():
    chunks = await RecursiveChunkingStrategy().chunk(_document(LONG_TEXT), config={}, chunk_id_prefix="idx:src")
    assert len(chunks) > 1
    assert all(chunk.metadata["unit_index"] == 0 for chunk in chunks)
    assert all(chunk.chunk_id.startswith("idx:src") for chunk in chunks)


@pytest.mark.asyncio
async def test_fixed_token_chunking_respects_target_and_overlap():
    chunks = await FixedTokenChunkingStrategy().chunk(
        _document(LONG_TEXT), config={"target_tokens": 32, "overlap_tokens": 4, "min_tokens": 1},
        chunk_id_prefix="idx:src",
    )
    assert len(chunks) > 1
    assert all(chunk.token_count <= 32 for chunk in chunks)


@pytest.mark.asyncio
async def test_structure_aware_chunking_sets_title_and_section():
    chunks = await StructureAwareChunkingStrategy().chunk(_document(LONG_TEXT), config={}, chunk_id_prefix="idx:src")
    assert chunks[0].title == "Sample Document"
    assert chunks[0].section == "unit-0"


@pytest.mark.asyncio
async def test_parent_child_chunking_links_children_to_one_parent():
    chunks = await ParentChildChunkingStrategy().chunk(
        _document(LONG_TEXT), config={"parent_tokens": 4096}, chunk_id_prefix="idx:src",
    )
    parents = [c for c in chunks if c.chunk_role == "parent"]
    children = [c for c in chunks if c.chunk_role == "child"]
    assert len(parents) == 1
    assert children
    assert all(child.parent_chunk_id == parents[0].chunk_id for child in children)
    # Parent-child chunks must not silently lose the searchable text.
    assert parents[0].embedding_content


@pytest.mark.asyncio
async def test_contextual_chunking_prepends_document_and_section_to_retrieval_content():
    chunks = await ContextualChunkingStrategy().chunk(_document(LONG_TEXT), config={}, chunk_id_prefix="idx:src")
    assert all(chunk.retrieval_content and chunk.retrieval_content.startswith("Document:") for chunk in chunks)
    # Raw text is preserved separately from the enriched retrieval surface.
    assert chunks[0].text != chunks[0].retrieval_content


@pytest.mark.asyncio
async def test_sentence_window_chunking_carries_surrounding_sentences_as_context():
    text = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
    chunks = await SentenceWindowChunkingStrategy().chunk(_document(text), config={"sentence_window": 1}, chunk_id_prefix="idx:src")
    assert len(chunks) == 5
    middle = chunks[2]
    assert middle.text == "Third sentence."
    assert "Second sentence." in middle.context_content
    assert "Fourth sentence." in middle.context_content


@pytest.mark.asyncio
async def test_semantic_chunking_splits_low_overlap_paragraphs_into_separate_segments():
    text = (
        "Sodium hypochlorite pumps require chemically resistant seals and diaphragms.\n\n"
        "Sodium hypochlorite pumps require chemically resistant valve materials too.\n\n"
        "Quarterly financial results improved due to currency exchange rate movements.\n\n"
        "Quarterly financial results also benefited from lower logistics costs."
    )
    chunks = await SemanticChunkingStrategy().chunk(
        _document(text), config={"min_tokens": 1}, chunk_id_prefix="idx:src"
    )
    labels = {chunk.metadata.get("unit_label") for chunk in chunks}
    # Two lexically distinct topics must not collapse into one segment.
    assert len(labels) >= 2


# ---- Stage registry --------------------------------------------------------

def test_default_stage_registry_has_every_documented_strategy():
    for name in ["standard", "layout_aware", "structure_aware", "ocr_fallback"]:
        assert DEFAULT_STAGE_REGISTRY.get_parser(name) is not None
    for name in ["fixed_token", "recursive", "structure_aware", "parent_child", "contextual", "sentence_window", "semantic"]:
        assert DEFAULT_STAGE_REGISTRY.get_chunker(name) is not None
    assert "metadata_context" in DEFAULT_STAGE_REGISTRY.enrichers


def test_stage_registry_raises_actionable_error_for_unknown_strategy():
    registry = StageRegistry()
    with pytest.raises(UnknownStrategyError):
        registry.get_chunker("nonexistent_strategy")


@pytest.mark.asyncio
async def test_metadata_context_enricher_only_prepends_when_configured():
    from app.ingestion.strategies import MetadataContextEnricher
    chunks = await RecursiveChunkingStrategy().chunk(_document(LONG_TEXT), config={}, chunk_id_prefix="idx:src")
    enricher = MetadataContextEnricher()
    untouched = await enricher.enrich([c for c in chunks], document=_document(LONG_TEXT), config={"prepend_context": False})
    assert all(chunk.retrieval_content is None for chunk in untouched)
    enriched = await enricher.enrich([c for c in chunks], document=_document(LONG_TEXT), config={"prepend_context": True})
    assert all(chunk.retrieval_content for chunk in enriched)
