"""Tests for the chunker. Edge cases first — chunking bugs are silent."""
from __future__ import annotations

import pytest

from app.ingestion.chunker import (
    Chunk,
    ChunkConfig,
    chunk_document,
    chunk_text,
)
from app.ingestion.extractor import ExtractedDocument, ExtractedUnit


# ---------- chunk_text() edge cases -------------------------------------------


def test_empty_text_returns_empty_list():
    assert chunk_text("") == []
    assert chunk_text("   \n  \n  ") == []


def test_very_short_text_below_min_returns_empty():
    """Text under min_tokens should be discarded, not returned."""
    cfg = ChunkConfig(min_tokens=50)
    assert chunk_text("Hi.", cfg) == []


def test_short_text_above_min_returns_one_chunk():
    """Reasonable text under target_tokens should return one chunk."""
    cfg = ChunkConfig(target_tokens=512, min_tokens=10)
    text = "This is a short paragraph. " * 5  # ~30 tokens
    chunks = chunk_text(text, cfg)
    assert len(chunks) == 1


def test_long_text_produces_multiple_chunks():
    """Text well above target should produce multiple chunks."""
    cfg = ChunkConfig(target_tokens=100, max_tokens=200, overlap_tokens=0, min_tokens=5)
    text = "Sentence number {i}. " * 200  # ~600 tokens
    text = " ".join(f"Sentence number {i}." for i in range(200))
    chunks = chunk_text(text, cfg)
    assert len(chunks) > 3


def test_chunks_respect_max_tokens():
    """No chunk should ever exceed max_tokens (with reasonable slack for overlap)."""
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    cfg = ChunkConfig(target_tokens=200, max_tokens=400, overlap_tokens=20, min_tokens=5)
    text = " ".join(f"Sentence number {i}." for i in range(500))
    chunks = chunk_text(text, cfg)
    # Allow overlap_tokens of slack: max + overlap is the real ceiling
    for c in chunks:
        assert len(enc.encode(c)) <= cfg.max_tokens + cfg.overlap_tokens


def test_section_headers_create_split_points():
    """Markdown headings should be honored as section boundaries."""
    text = (
        "# Section One\n"
        "Some content here that's reasonably long. " * 30
        + "\n\n# Section Two\n"
        + "More content in section two. " * 30
    )
    cfg = ChunkConfig(target_tokens=100, max_tokens=200, overlap_tokens=0)
    chunks = chunk_text(text, cfg)
    # First chunk should start with the first section header (or its content)
    assert "Section One" in chunks[0]
    # At least one later chunk should contain Section Two
    assert any("Section Two" in c for c in chunks)


def test_overlap_appears_in_adjacent_chunks():
    """Each chunk after the first should start with content from the previous chunk."""
    cfg = ChunkConfig(target_tokens=50, max_tokens=100, overlap_tokens=10, min_tokens=5)
    text = " ".join(f"Token{i}" for i in range(500))
    chunks = chunk_text(text, cfg)
    if len(chunks) >= 2:
        # The start of chunk 1 should contain content from end of chunk 0
        # (we can't check exact tokens since tokenization may differ from word splits)
        # but the chunks must overlap textually
        last_words_of_first = chunks[0].split()[-5:]
        first_n_chars_of_second = chunks[1][: len(" ".join(last_words_of_first)) + 20]
        assert any(w in first_n_chars_of_second for w in last_words_of_first)


def test_zero_overlap_no_repeated_content():
    """With overlap=0, adjacent chunks should not share content."""
    cfg = ChunkConfig(target_tokens=100, max_tokens=150, overlap_tokens=0, min_tokens=5)
    text = " ".join(f"Word{i}" for i in range(300))
    chunks = chunk_text(text, cfg)
    if len(chunks) >= 2:
        last_word = chunks[0].split()[-1]
        # The exact last word of chunk 0 should NOT be the first word of chunk 1
        # (assuming our splitter is doing its job)
        assert chunks[1].split()[0] != last_word


# ---------- chunk_document() edge cases ---------------------------------------


def test_chunk_document_with_one_unit():
    doc = ExtractedDocument(
        source_path="/test/doc.pdf",
        source_format="pdf",
        page_count=1,
        units=[ExtractedUnit(index=0, label="page 1", text="A. " * 200)],
        metadata={"industry": "mining"},
    )
    chunks = chunk_document(doc)
    assert len(chunks) >= 1
    assert all(isinstance(c, Chunk) for c in chunks)
    # Metadata inheritance check
    assert chunks[0].metadata["doc_industry"] == "mining"
    # ID format check
    assert chunks[0].chunk_id.startswith("/test/doc.pdf::unit0::chunk")


def test_chunk_document_units_do_not_cross():
    """Chunks should never span two units (page/sheet boundary)."""
    doc = ExtractedDocument(
        source_path="/test/multi.pdf",
        source_format="pdf",
        page_count=2,
        units=[
            ExtractedUnit(index=0, label="page 1", text="Alpha " * 100),
            ExtractedUnit(index=1, label="page 2", text="Beta " * 100),
        ],
    )
    chunks = chunk_document(doc)
    # No chunk should contain BOTH "Alpha" and "Beta" — that would mean
    # the chunker crossed a unit boundary.
    for c in chunks:
        assert not ("Alpha" in c.text and "Beta" in c.text), (
            f"Chunk crossed unit boundary: {c.chunk_id}"
        )


def test_empty_units_are_skipped():
    doc = ExtractedDocument(
        source_path="/test/sparse.pdf",
        source_format="pdf",
        page_count=3,
        units=[
            ExtractedUnit(index=0, label="page 1", text="Alpha. " * 100),
            ExtractedUnit(index=1, label="page 2", text=""),  # scanned page
            ExtractedUnit(index=2, label="page 3", text="Gamma. " * 100),
        ],
    )
    chunks = chunk_document(doc)
    # Should produce chunks only for units 0 and 2
    unit_indexes_seen = {c.metadata["unit_index"] for c in chunks}
    assert 0 in unit_indexes_seen
    assert 2 in unit_indexes_seen
    assert 1 not in unit_indexes_seen


def test_chunk_ids_are_unique():
    doc = ExtractedDocument(
        source_path="/test/multi.pdf",
        source_format="pdf",
        page_count=3,
        units=[
            ExtractedUnit(index=i, label=f"page {i+1}", text="Foo. " * 200)
            for i in range(3)
        ],
    )
    chunks = chunk_document(doc)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "Duplicate chunk IDs detected"


def test_chunk_ids_are_deterministic():
    """Same input produces same IDs — required for idempotent ingestion."""
    doc = ExtractedDocument(
        source_path="/test/doc.pdf",
        source_format="pdf",
        page_count=1,
        units=[ExtractedUnit(index=0, label="page 1", text="X. " * 200)],
    )
    chunks_a = chunk_document(doc)
    chunks_b = chunk_document(doc)
    assert [c.chunk_id for c in chunks_a] == [c.chunk_id for c in chunks_b]