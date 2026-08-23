"""Idempotent legacy -> Knowledge Studio resource backfill.

Converts existing ``CollectionConfig`` records (Mongo ``collections``) and
ingestion ``manifests`` into first-class Knowledge Studio resources — a
``CollectionResource``, a "Legacy Index v1" ``IndexVersion`` per owner scope,
and a ``DocumentResource``/``SourceVersionResource`` per manifest — without:

* changing any existing collection_id (it is reused verbatim, never reminted)
* discarding manifest provenance (minio_key, chunk_ids, byte_size, hashes)
* inventing an owner for data that never recorded one
* forcing re-ingestion (existing Weaviate objects keep working through the
  new Legacy Index v1, which intentionally omits a physical_index_id filter)

Owner scope is derived from ``manifest["metadata"]["session_id"]`` — the same
field the legacy ingestion pipeline (``app/ingestion/pipeline.py``) already
stamps on every manifest, defaulting to ``"default"`` when the metadata never
specified one. That default is the legacy pipeline's own convention, not
something invented here.

Usage:
    # Preview only — no writes.
    uv run python scripts/backfill_knowledge_resources.py

    # Apply.
    uv run python scripts/backfill_knowledge_resources.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from app.db.mongo import DB_NAME, get_mongo_client
from app.knowledge.models import (
    CollectionResource,
    DocumentResource,
    ResourceStatus,
    SourceVersionResource,
)
from app.knowledge.repository import KnowledgeRepository, ResourceNotFoundError
from app.knowledge.service import KnowledgeService, workspace_for_scope
from app.knowledge.ids import new_resource_id
from app.observability.logging import get_logger
from app.retrieval.weaviate_client import COLLECTION_NAME

log = get_logger(__name__)

DEFAULT_LEGACY_OWNER = "default"


async def _load_legacy_collection_configs(db) -> dict[str, dict]:
    """Load the legacy collection configs.

    Args:
        db: Mongo database handle.

    Returns:
        dict[str, dict]: The legacy collection configs.
    """
    configs: dict[str, dict] = {}
    async for doc in db["collections"].find({}):
        configs[doc["collection_id"]] = doc
    return configs


async def _group_manifests_by_owner_and_collection(db) -> dict[tuple[str, str], list[dict]]:
    """Group the manifests by owner and collection.

    Args:
        db: Mongo database handle.

    Returns:
        dict[tuple[str, str], list[dict]]: The manifests by owner and collection.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    async for manifest in db["manifests"].find({}):
        metadata = manifest.get("metadata") or {}
        owner_scope_id = str(metadata.get("session_id") or DEFAULT_LEGACY_OWNER)
        collection_id = str(metadata.get("collection_id") or DEFAULT_LEGACY_OWNER)
        groups[(owner_scope_id, collection_id)].append(manifest)
    return groups


async def backfill(*, apply: bool, db=None) -> dict[str, int]:
    """Backfill the result.

    Args:
        apply (bool): The apply.
        db: Mongo database handle (optional, default None).

    Returns:
        dict[str, int]: The result.
    """
    if db is None:
        mongo = get_mongo_client()
        db = mongo._ensure_client()[DB_NAME]
    repository = KnowledgeRepository(db)
    service = KnowledgeService(repository)

    legacy_configs = await _load_legacy_collection_configs(db)
    groups = await _group_manifests_by_owner_and_collection(db)

    stats = {"groups_seen": len(groups), "collections_created": 0, "collections_existing": 0,
              "documents_created": 0, "unregistered_collection_ids": 0}

    for (owner_scope_id, collection_id), manifests in groups.items():
        try:
            await repository.get_collection(owner_scope_id, collection_id)
            stats["collections_existing"] += 1
            log.info(
                "backfill.collection_already_exists",
                owner_scope_id=owner_scope_id, collection_id=collection_id,
            )
            continue
        except ResourceNotFoundError:
            pass

        legacy = legacy_configs.get(collection_id)
        if legacy is None:
            stats["unregistered_collection_ids"] += 1
        name = legacy["display_name"] if legacy else collection_id
        doc_types = legacy["doc_types"] if legacy else ["general"]
        description = (
            legacy.get("description", "") if legacy
            else "Backfilled from ingestion manifests; no matching CollectionConfig was registered."
        )
        default_industry = legacy.get("default_industry") if legacy else None

        total_bytes = sum(int(m.get("byte_size") or 0) for m in manifests)
        total_chunks = sum(int(m.get("chunk_count") or len(m.get("chunk_ids") or [])) for m in manifests)
        log.info(
            "backfill.plan_collection",
            owner_scope_id=owner_scope_id, collection_id=collection_id, name=name,
            documents=len(manifests), chunks=total_chunks, bytes=total_bytes,
        )
        if not apply:
            stats["collections_created"] += 1
            stats["documents_created"] += len(manifests)
            continue

        # Collection: the legacy collection_id is reused verbatim, never
        # re-minted — this is the one identifier this script must preserve.
        collection = await repository.create_collection(
            CollectionResource(
                collection_id=collection_id,
                workspace_id=workspace_for_scope(owner_scope_id),
                owner_scope_id=owner_scope_id,
                name=name,
                description=description,
                doc_types=doc_types,
                default_industry=default_industry,
                legacy_aliases=[collection_id],
                status=ResourceStatus.BUILDING,
            )
        )

        defaults = await service.ensure_default_profiles(owner_scope_id)
        index = await service.create_index(
            owner_scope_id=owner_scope_id, collection_id=collection_id,
            parser_profile_id=defaults["parser"].profile_id, parser_profile_version=defaults["parser"].version,
            chunking_profile_id=defaults["chunking"].profile_id, chunking_profile_version=defaults["chunking"].version,
            embedding_profile_id=defaults["embedding"].profile_id, embedding_profile_version=defaults["embedding"].version,
        )
        # Legacy objects predate index_id and live in the one shared
        # DocumentChunk collection, not a fingerprinted physical collection.
        index.physical_index_id = None
        index.physical_collection = COLLECTION_NAME
        index.status = ResourceStatus.READY
        index.document_count = len(manifests)
        index.chunk_count = total_chunks
        await repository.save_index(index)

        for manifest in manifests:
            document_id = new_resource_id("document")
            source_id = new_resource_id("source")
            source_version_id = new_resource_id("source_version")
            filename = manifest.get("original_filename") or manifest["minio_key"]
            source_format = manifest.get("source_format") or ""
            content_hash = manifest["minio_key"].split(":", 1)[-1] if ":" in manifest["minio_key"] else manifest["minio_key"]
            document = DocumentResource(
                document_id=document_id, source_id=source_id, collection_id=collection_id,
                filename=filename, mime_type="application/octet-stream", source_format=source_format,
                content_hash=content_hash, current_source_version_id=source_version_id,
                metadata=manifest.get("metadata") or {}, workspace_id=collection.workspace_id,
                owner_scope_id=owner_scope_id, status=ResourceStatus.READY,
            )
            await repository.save_document(document)
            await repository.save_source_version(SourceVersionResource(
                source_version_id=source_version_id, source_id=source_id, document_id=document_id,
                collection_id=collection_id, version=1, filename=filename,
                mime_type="application/octet-stream", source_format=source_format,
                storage_key=manifest["minio_key"], content_hash=content_hash,
                byte_size=int(manifest.get("byte_size") or 0), metadata=manifest.get("metadata") or {},
                workspace_id=collection.workspace_id, owner_scope_id=owner_scope_id,
            ))
            await repository.save_index_document(
                owner_scope_id=owner_scope_id, index_id=index.index_id, document_id=document_id,
                source_version_id=source_version_id, ingestion_job_id="legacy-backfill",
                chunk_ids=list(manifest.get("chunk_ids") or []),
            )
            stats["documents_created"] += 1

        collection.active_index_id = index.index_id
        collection.status = ResourceStatus.READY
        collection.document_count = index.document_count
        collection.chunk_count = index.chunk_count
        await repository.save_collection(collection)
        index.status = ResourceStatus.ACTIVE
        index.activated_at = datetime.now(timezone.utc)
        await repository.save_index(index)
        stats["collections_created"] += 1

    return stats


async def main() -> None:
    """Compute the main."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes. Omit for a dry-run preview.")
    args = parser.parse_args()

    stats = await backfill(apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY RUN — no changes were written"
    print(f"\nKnowledge Studio legacy backfill: {mode}")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    if not args.apply:
        print("\nRe-run with --apply to write these changes.")


if __name__ == "__main__":
    asyncio.run(main())
