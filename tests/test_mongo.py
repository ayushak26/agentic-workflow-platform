"""Tests for the Mongo manifest store. Uses the live awp-mongo container."""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.db.mongo import MongoClient, build_manifest
from app.config import settings    


@pytest_asyncio.fixture
async def mongo():
    """Fresh client per test. Wipes the manifests collection before each test."""
    client = MongoClient(settings.mongo_uri)
    await client.ensure_indexes()
    await client.manifests.delete_many({})  # clean slate
    yield client
    await client.manifests.delete_many({})
    await client.close()


@pytest.mark.asyncio
async def test_build_manifest_shape():
    m = build_manifest(
        minio_key="sha256:abc.pdf",
        original_filename="rfp.pdf",
        source_format="pdf",
        byte_size=12345,
        chunk_ids=["c1", "c2", "c3"],
        metadata={"industry": "mining"},
    )
    assert m["_id"] == "sha256:abc.pdf"
    assert m["chunk_count"] == 3
    assert m["status"] == "completed"
    assert m["metadata"]["industry"] == "mining"
    assert "ingested_at" in m


@pytest.mark.asyncio
async def test_upsert_and_get(mongo):
    m = build_manifest(
        minio_key="sha256:test1.pdf",
        original_filename="test1.pdf",
        source_format="pdf",
        byte_size=100,
        chunk_ids=["c1"],
    )
    await mongo.upsert_manifest(m)
    fetched = await mongo.get_manifest("sha256:test1.pdf")
    assert fetched is not None
    assert fetched["original_filename"] == "test1.pdf"


@pytest.mark.asyncio
async def test_upsert_is_idempotent(mongo):
    """Same _id upserted twice = 1 row, not 2."""
    m = build_manifest(
        minio_key="sha256:dup.pdf",
        original_filename="dup.pdf",
        source_format="pdf",
        byte_size=100,
        chunk_ids=["c1"],
    )
    await mongo.upsert_manifest(m)
    await mongo.upsert_manifest(m)
    all_manifests = await mongo.list_manifests()
    assert len(all_manifests) == 1


@pytest.mark.asyncio
async def test_list_filtered_by_status(mongo):
    completed = build_manifest("k1", "a.pdf", "pdf", 1, ["c"], status="completed")
    failed = build_manifest("k2", "b.pdf", "pdf", 1, ["c"], status="failed")
    await mongo.upsert_manifest(completed)
    await mongo.upsert_manifest(failed)

    only_completed = await mongo.list_manifests(status="completed")
    assert len(only_completed) == 1
    assert only_completed[0]["_id"] == "k1"


@pytest.mark.asyncio
async def test_mark_status_updates_field(mongo):
    m = build_manifest("k_progress", "x.pdf", "pdf", 1, [], status="in_progress")
    await mongo.upsert_manifest(m)
    await mongo.mark_status("k_progress", "completed")
    fetched = await mongo.get_manifest("k_progress")
    assert fetched["status"] == "completed"


@pytest.mark.asyncio
async def test_delete(mongo):
    m = build_manifest("k_del", "y.pdf", "pdf", 1, [], status="completed")
    await mongo.upsert_manifest(m)
    deleted = await mongo.delete_manifest("k_del")
    assert deleted is True
    fetched = await mongo.get_manifest("k_del")
    assert fetched is None