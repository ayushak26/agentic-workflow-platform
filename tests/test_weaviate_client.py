"""Tests for the Weaviate client and schema."""
from __future__ import annotations

import pytest

from app.retrieval.weaviate_client import (
    COLLECTION_NAME,
    SCHEMA_PROPERTIES,
    WeaviateClient,
)


@pytest.fixture
def client():
    """Fresh Weaviate client per test, with schema reset between tests."""
    c = WeaviateClient()
    c.connect()
    c.reset_schema()
    yield c
    c.close()
    c.reset_schema()
    c.close()


def test_schema_has_expected_properties():
    """Schema must include every property the chunker emits."""
    names = {p.name for p in SCHEMA_PROPERTIES}
    expected = {
        "chunk_id", "text", "token_count",
        "source_path", "source_format", "unit_index", "unit_label", "chunk_index",
        "industry", "doc_type", "language",
    }
    assert expected.issubset(names), f"missing properties: {expected - names}"


def test_collection_creation_is_idempotent(client):
    """Calling ensure_schema twice is safe."""
    client.ensure_schema()
    client.ensure_schema()
    assert client.connect().collections.exists(COLLECTION_NAME)


def test_upsert_and_count(client):
    """Insert two chunks with vectors, count returns 2."""
    chunks = [
        {
            "chunk_id": "test::chunk0",
            "text": "first chunk content",
            "token_count": 10,
            "source_path": "/test/doc.txt",
            "source_format": "txt",
            "unit_index": 0,
            "unit_label": "document",
            "chunk_index": 0,
            "industry": "test",
            "doc_type": "test",
            "language": "",
        },
        {
            "chunk_id": "test::chunk1",
            "text": "second chunk content",
            "token_count": 10,
            "source_path": "/test/doc.txt",
            "source_format": "txt",
            "unit_index": 0,
            "unit_label": "document",
            "chunk_index": 1,
            "industry": "test",
            "doc_type": "test",
            "language": "",
        },
    ]
    vectors = [[0.1] * 1536, [0.2] * 1536]
    inserted = client.upsert_chunks(chunks, vectors)
    assert inserted == 2
    assert client.count_chunks() == 2


def test_upsert_is_idempotent(client):
    """Same chunk_id inserted twice = 1 row, not 2."""
    chunk = {
        "chunk_id": "test::single",
        "text": "content",
        "token_count": 5,
        "source_path": "/test/doc.txt",
        "source_format": "txt",
        "unit_index": 0,
        "unit_label": "document",
        "chunk_index": 0,
        "industry": "test",
        "doc_type": "test",
        "language": "",
    }
    vector = [0.5] * 1536
    client.upsert_chunks([chunk], [vector])
    client.upsert_chunks([chunk], [vector])
    assert client.count_chunks() == 1