"""Tests for the Weaviate client and schema."""
from __future__ import annotations

import pytest

from app.retrieval.weaviate_client import (
    COLLECTION_NAME,
    SCHEMA_PROPERTIES,
    VECTOR_NAME,
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

def test_vectors_are_written_to_the_named_target(client):
    """Vectors must be searchable, not merely stored.

    Regression: objects were written through the legacy single-vector argument
    while the schema declared a named vector, so Weaviate accepted the write
    but left the named vector's HNSW index empty. near_vector then failed with
    "vector not found for target: default" and dense search silently returned
    nothing, leaving hybrid search quietly BM25-only.
    """
    client.ensure_schema()
    chunks = [
        {
            "chunk_id": "vec::chunk0",
            "text": "PTFE seals resist sulphuric acid",
            "token_count": 8,
            "source_path": "/test/seals.txt",
            "source_format": "txt",
            "unit_index": 0,
            "unit_label": "document",
            "chunk_index": 0,
        }
    ]
    vector = [0.1] * 1536
    client.upsert_chunks(chunks, [vector])

    collection = client.connect().collections.get(COLLECTION_NAME)
    stored = collection.query.fetch_objects(limit=1, include_vector=True).objects[0]
    assert VECTOR_NAME in stored.vector, f"vector not stored under {VECTOR_NAME!r}"

    # The real assertion: the vector index can be searched.
    found = collection.query.near_vector(
        near_vector=vector, target_vector=VECTOR_NAME, limit=1
    )
    assert found.objects, "near_vector returned nothing — named vector index is empty"
    assert found.objects[0].properties["chunk_id"] == "vec::chunk0"
