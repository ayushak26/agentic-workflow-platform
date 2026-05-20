"""End-to-end pipeline integration test.

Exercises every real subsystem: extractor -> chunker -> embedder -> MinIO ->
Weaviate -> Mongo. Uses a small synthetic markdown file generated in-test
to keep the test isolated from external fixtures.

Skipped if OPENAI_API_KEY is not set.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.db.mongo import get_mongo_client
from app.ingestion.pipeline import ingest_file
from app.retrieval.weaviate_client import get_weaviate_client


pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY required for embedder calls",
)


SAMPLE_MARKDOWN = """# Test Document for Integration

## Section A: Introduction

This document exists only to test the ingestion pipeline end-to-end.
It contains enough content to produce at least one chunk, with sections
that should be respected by the chunker.

## Section B: Domain Content

The mining industry uses haul trucks to move ore from open pits to
the processing plant. Operational efficiency is measured in tonnes per
hour and equipment utilization rate.

## Section C: More Content to Force Chunking

""" + ("Generic prose to expand the document length. " * 30) + """

## Section D: Closing

The end of the test document.
"""


@pytest.mark.asyncio
async def test_pipeline_end_to_end(tmp_path: Path):
    """Ingest a synthetic doc; verify every layer landed the right data.

    Asserts in order:
      1. Pipeline returns 'completed' status with chunks > 0
      2. Mongo manifest exists with completed status
      3. Weaviate has the chunks
      4. Re-running is idempotent (skipped=True, no new chunks)
    """
    # Setup: clean slate
    weaviate = get_weaviate_client()
    weaviate.reset_schema()
    mongo = get_mongo_client()
    await mongo.manifests.delete_many({})

    # Write the sample to a temp file
    test_file = tmp_path / "integration_test.md"
    test_file.write_text(SAMPLE_MARKDOWN)

    # Act 1: ingest
    result = await ingest_file(
        test_file,
        metadata={"industry": "mining", "doc_type": "test"},
    )

    # Assert 1: pipeline result
    assert result.status == "completed"
    assert result.chunk_count > 0
    assert result.skipped is False
    assert len(result.chunk_ids) == result.chunk_count

    # Assert 2: manifest in Mongo
    manifest = await mongo.get_manifest(result.minio_key)
    assert manifest is not None
    assert manifest["status"] == "completed"
    assert manifest["original_filename"] == "integration_test.md"
    assert manifest["metadata"]["industry"] == "mining"
    assert manifest["chunk_count"] == result.chunk_count

    # Assert 3: chunks in Weaviate
    weaviate_count = weaviate.count_chunks()
    assert weaviate_count == result.chunk_count

    # Act 2: re-ingest the same file
    result2 = await ingest_file(
        test_file,
        metadata={"industry": "mining", "doc_type": "test"},
    )

    # Assert 4: idempotency
    assert result2.skipped is True
    assert result2.minio_key == result.minio_key
    assert result2.chunk_count == result.chunk_count
    assert weaviate.count_chunks() == result.chunk_count

    # Cleanup
    weaviate.reset_schema()
    await mongo.manifests.delete_many({})
    await mongo.close()