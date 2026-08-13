"""Legacy CollectionConfig/manifest -> Knowledge Studio resource backfill."""
from __future__ import annotations

import pytest

from app.db.mongo import build_manifest
from app.knowledge.models import ResourceStatus
from app.knowledge.repository import KnowledgeRepository
from app.retrieval.weaviate_client import COLLECTION_NAME
from scripts.backfill_knowledge_resources import backfill
from tests.fake_mongo import InMemoryDB


async def _seed_legacy_data(db: InMemoryDB) -> None:
    await db["collections"].insert_one({
        "collection_id": "proposals_default",
        "display_name": "Default Proposal Corpus",
        "doc_types": ["general", "reference"],
        "default_industry": "consulting",
        "description": "Legacy proposal knowledge base.",
    })
    manifest = build_manifest(
        minio_key="sha256:deadbeef.pdf",
        original_filename="prior-project.pdf",
        source_format="pdf",
        byte_size=4096,
        chunk_ids=["chunk-1", "chunk-2"],
        metadata={"session_id": "session-a", "collection_id": "proposals_default"},
    )
    await db["manifests"].insert_one(manifest)


@pytest.mark.asyncio
async def test_dry_run_reports_planned_work_without_writing_anything():
    db = InMemoryDB()
    await _seed_legacy_data(db)
    stats = await backfill(apply=False, db=db)
    assert stats["collections_created"] == 1
    assert stats["documents_created"] == 1
    assert stats["unregistered_collection_ids"] == 0

    repository = KnowledgeRepository(db)
    from app.knowledge.repository import ResourceNotFoundError
    with pytest.raises(ResourceNotFoundError):
        await repository.get_collection("session-a", "proposals_default")


@pytest.mark.asyncio
async def test_apply_preserves_the_legacy_collection_id_and_creates_a_ready_active_index():
    db = InMemoryDB()
    await _seed_legacy_data(db)
    stats = await backfill(apply=True, db=db)
    assert stats["collections_created"] == 1

    repository = KnowledgeRepository(db)
    collection = await repository.get_collection("session-a", "proposals_default")
    assert collection.collection_id == "proposals_default"  # never re-minted
    assert collection.name == "Default Proposal Corpus"
    assert collection.doc_types == ["general", "reference"]
    assert collection.active_index_id is not None
    assert collection.document_count == 1
    assert collection.chunk_count == 2

    index = await repository.get_index("session-a", collection.active_index_id)
    assert index.status == ResourceStatus.ACTIVE
    assert index.physical_index_id is None  # legacy objects predate index_id
    assert index.physical_collection == COLLECTION_NAME  # shared legacy collection, not fingerprinted

    documents = await repository.list_documents("session-a", "proposals_default")
    assert len(documents) == 1
    assert documents[0].filename == "prior-project.pdf"
    assert documents[0].status == ResourceStatus.READY


@pytest.mark.asyncio
async def test_apply_is_idempotent_on_a_second_run():
    db = InMemoryDB()
    await _seed_legacy_data(db)
    await backfill(apply=True, db=db)
    second_run = await backfill(apply=True, db=db)
    assert second_run["collections_created"] == 0
    assert second_run["collections_existing"] == 1


@pytest.mark.asyncio
async def test_manifest_owner_defaults_to_the_legacy_pipelines_own_default_session():
    db = InMemoryDB()
    manifest = build_manifest(
        minio_key="sha256:cafef00d.txt", original_filename="unscoped.txt",
        source_format="txt", byte_size=10, chunk_ids=["chunk-1"], metadata={},
    )
    await db["manifests"].insert_one(manifest)
    stats = await backfill(apply=True, db=db)
    assert stats["collections_created"] == 1
    assert stats["unregistered_collection_ids"] == 1  # no CollectionConfig was ever registered

    repository = KnowledgeRepository(db)
    # "default" is the legacy ingestion pipeline's own fallback, not an
    # identity invented by the backfill.
    collection = await repository.get_collection("default", "default")
    assert collection.name == "default"
