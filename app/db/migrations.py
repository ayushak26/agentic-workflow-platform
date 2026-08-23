"""Versioned, idempotent MongoDB data migrations.

Index creation is not a data-migration strategy: old documents keep their old
shape. This registry runs before the API starts accepting traffic, records each
completed migration, and uses a Mongo lease so concurrent Uvicorn workers do
not backfill the same population at once.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.observability.logging import get_logger

log = get_logger(__name__)

MIGRATION_COLLECTION = "schema_migrations"
CURRENT_RUN_SCHEMA_VERSION = 1
_LOCK_ID = "__migration_lock__"
_LOCK_SECONDS = 300


class MigrationError(RuntimeError):
    """Raised when the database cannot be brought to the required schema."""


@dataclass(frozen=True)
class Migration:
    """Provides the Migration behaviour.

    Attributes:
        migration_id (str).
        description (str).
        apply (Callable[[Any], Awaitable[None]]).
    """
    migration_id: str
    description: str
    apply: Callable[[Any], Awaitable[None]]


async def _set_missing(collection: Any, field: str, value: Any) -> None:
    """Set the missing.

    Args:
        collection (Any): Mongo collection.
        field (str): The field.
        value (Any): Value to process.
    """
    await collection.update_many(
        {field: {"$exists": False}},
        {"$set": {field: value}},
    )


async def _backfill_run_documents_v1(db: Any) -> None:
    """Give legacy run and checkpoint documents the explicit v1 shape."""

    history_defaults = {
        "active_nodes": [],
        "completed_nodes": [],
        "node_runs": {},
        "outputs": {},
        "completed_node_count": 0,
        "reused_node_count": 0,
        "reused_nodes": [],
        "attempt": 1,
        "error": None,
        "schema_version": CURRENT_RUN_SCHEMA_VERSION,
    }
    checkpoint_defaults = {
        "node_results": {},
        "completed_nodes": [],
        "approvals": [],
        "schema_version": CURRENT_RUN_SCHEMA_VERSION,
    }
    for field, value in history_defaults.items():
        await _set_missing(db["run_history"], field, value)
    for field, value in checkpoint_defaults.items():
        await _set_missing(db["run_checkpoints"], field, value)


async def _backfill_knowledge_resources_v2(db: Any) -> None:
    """Expand legacy ``collections`` (CollectionConfig) records with the
    lifecycle fields Knowledge Studio needs.

    Identifiers, names and ownership are never touched — this only adds
    fields that are missing (``$exists: false``), the same idempotent
    pattern as the v1 run-document backfill above. Legacy records that were
    always globally scoped (no ``owner_scope_id``) are expanded the same
    way; this migration never assigns one where none exists.
    """

    defaults = {
        "status": "ready",
        "document_count": 0,
        "chunk_count": 0,
        "active_index_id": None,
        "metadata_schema": {},
        "doc_types": ["general"],
        "legacy_aliases": [],
        "schema_version": 2,
    }
    for field, value in defaults.items():
        await _set_missing(db["collections"], field, value)


_REMOVED_PRODUCT_COLLECTIONS = (
    "pipeline_runs",
    "business_narrations",
)

_REMOVED_RUN_FIELDS = (
    "pipeline_run_id",
    "pipeline_name",
    "stage_id",
    "stage_index",
    "business_notes",
    "route_overrides",
    "assigned_to",
    "fact_edits",
    "stale_decisions",
)


async def _remove_business_and_pipeline_persistence_v3(db: Any) -> None:
    """Remove persistence owned exclusively by the retired product concepts.

    ``stage_id`` is removed only as a top-level run-history field formerly used
    for Pipeline stage linkage. Workflow YAML is not rewritten, so nested
    ``experience.stage_id`` presentation grouping remains intact.
    """

    await db["run_history"].update_many(
        {},
        {"$unset": {field: "" for field in _REMOVED_RUN_FIELDS}},
    )
    for collection_name in _REMOVED_PRODUCT_COLLECTIONS:
        await db.drop_collection(collection_name)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        migration_id="0001_run_documents_v1",
        description="Backfill explicit v1 run-history and checkpoint fields",
        apply=_backfill_run_documents_v1,
    ),
    Migration(
        migration_id="0002_knowledge_resources_v2",
        description="Expand legacy collection records with Knowledge Studio v2 lifecycle fields",
        apply=_backfill_knowledge_resources_v2,
    ),
    Migration(
        migration_id="0003_remove_business_pipeline_persistence",
        description="Drop retired Business View and Pipeline persistence",
        apply=_remove_business_and_pipeline_persistence_v3,
    ),
)


async def _acquire_lock(
    collection: Any,
    *,
    owner: str,
    wait_timeout_seconds: float,
) -> None:
    """Acquire the lock.

    Args:
        collection (Any): Mongo collection.
        owner (str): Lease owner identifier.
        wait_timeout_seconds (float): The wait timeout seconds.
    """
    deadline = asyncio.get_running_loop().time() + wait_timeout_seconds
    while True:
        now = datetime.now(timezone.utc)
        try:
            document = await collection.find_one_and_update(
                {
                    "_id": _LOCK_ID,
                    "$or": [
                        {"owner": owner},
                        {"lease_expires_at": {"$lte": now}},
                        {"lease_expires_at": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "owner": owner,
                        "lease_expires_at": now + timedelta(seconds=_LOCK_SECONDS),
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            document = None
        if document is not None and document.get("owner") == owner:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise MigrationError("Timed out waiting for the Mongo migration lease")
        await asyncio.sleep(0.1)


async def _renew_lock(collection: Any, *, owner: str) -> None:
    """Renew the lock.

    Args:
        collection (Any): Mongo collection.
        owner (str): Lease owner identifier.
    """
    now = datetime.now(timezone.utc)
    result = await collection.update_one(
        {"_id": _LOCK_ID, "owner": owner},
        {
            "$set": {
                "lease_expires_at": now + timedelta(seconds=_LOCK_SECONDS),
                "updated_at": now,
            }
        },
    )
    if getattr(result, "matched_count", 0) != 1:
        raise MigrationError("Mongo migration lease was lost")


async def run_migrations(
    db: Any,
    *,
    owner: str | None = None,
    wait_timeout_seconds: float = 60.0,
) -> list[str]:
    """Run every pending migration and return the applied migration ids."""

    owner = owner or uuid.uuid4().hex
    collection = db[MIGRATION_COLLECTION]
    await _acquire_lock(
        collection,
        owner=owner,
        wait_timeout_seconds=wait_timeout_seconds,
    )
    applied: list[str] = []
    try:
        for migration in MIGRATIONS:
            await _renew_lock(collection, owner=owner)
            existing = await collection.find_one(
                {"_id": migration.migration_id, "status": "completed"}
            )
            if existing is not None:
                continue

            now = datetime.now(timezone.utc)
            await collection.update_one(
                {"_id": migration.migration_id},
                {
                    "$set": {
                        "status": "running",
                        "description": migration.description,
                        "owner": owner,
                        "started_at": now,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
            try:
                await migration.apply(db)
            except Exception as exc:
                await collection.update_one(
                    {"_id": migration.migration_id, "owner": owner},
                    {
                        "$set": {
                            "status": "failed",
                            "error": str(exc)[:1000],
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                )
                raise MigrationError(
                    f"Migration {migration.migration_id} failed"
                ) from exc

            completed_at = datetime.now(timezone.utc)
            await collection.update_one(
                {"_id": migration.migration_id, "owner": owner},
                {
                    "$set": {
                        "status": "completed",
                        "completed_at": completed_at,
                        "updated_at": completed_at,
                    },
                    "$unset": {"error": ""},
                },
            )
            applied.append(migration.migration_id)
            log.info("mongo.migration_completed", migration=migration.migration_id)
    finally:
        await collection.delete_one({"_id": _LOCK_ID, "owner": owner})
    return applied