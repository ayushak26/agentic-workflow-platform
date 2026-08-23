"""Async MongoDB client and manifest collection.

The 'manifests' collection records one document per ingested file:
  - _id = MinIO object key (deterministic, content-addressed)
  - original_filename, byte_size, source_format
  - chunk_ids (list of Weaviate chunk IDs)
  - status (in_progress, completed, failed)
  - ingested_at, metadata

Manifest gives us: 'what's been ingested?', 'where did chunk X come from?',
'how do I delete all chunks for file Y?'. Without it, the pipeline is opaque.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.observability.logging import get_logger

log = get_logger(__name__)

# Derived from settings so the motor-based handles (manifests, scorecards,
# audit_db, knowledge) always target the same database as the sync pymongo
# handle built in app/main.py — a hardcoded name here would silently
# split-brain the data if MONGO_DB were ever customised.
DB_NAME = settings.mongo_db
MANIFESTS_COLLECTION = "manifests"
SCORECARDS_COLLECTION = "scorecards"
COLLECTIONS_COLLECTION = "collections"

# Knowledge Studio control-plane collections. The legacy `manifests` and
# `collections` collections above stay authoritative for existing provenance;
# these add separately lifecycle-managed resources beside them. Logical
# Collection resources get their own collection rather than reusing the
# legacy `collections` one, whose documents follow the older CollectionConfig
# shape and must not be overwritten by the newer, stricter schema.
KNOWLEDGE_COLLECTIONS_COLLECTION = "knowledge_collections"
KNOWLEDGE_DOCUMENTS_COLLECTION = "knowledge_documents"
KNOWLEDGE_SOURCE_VERSIONS_COLLECTION = "knowledge_source_versions"
KNOWLEDGE_PROFILES_COLLECTION = "knowledge_profiles"
KNOWLEDGE_INDEXES_COLLECTION = "knowledge_indexes"
KNOWLEDGE_INDEX_DOCUMENTS_COLLECTION = "knowledge_index_documents"
INGESTION_JOBS_COLLECTION = "ingestion_jobs"
RAG_AGENTS_COLLECTION = "rag_agents"
RETRIEVAL_TRACES_COLLECTION = "retrieval_traces"


class MongoClient:
    """Async Mongo wrapper. One per app instance.

    Owns the AsyncIOMotorClient lifecycle. Provides typed CRUD over the
    manifests collection plus index management.
    """

    def __init__(self, url: str | None = None):
        """Initialize the MongoClient.

        Args:
            url (str | None): Target URL (optional, default None).
        """
        self._url = url or settings.mongo_uri
        self._client: AsyncIOMotorClient | None = None

    def _ensure_client(self) -> AsyncIOMotorClient:
        """Ensure the client.

        Returns:
            AsyncIOMotorClient: The client.
        """
        if self._client is None:
            timeout_ms = int(settings.external_request_timeout_seconds * 1_000)
            self._client = AsyncIOMotorClient(
                self._url,
                serverSelectionTimeoutMS=timeout_ms,
                connectTimeoutMS=timeout_ms,
                socketTimeoutMS=timeout_ms,
            )
            # Never log the URI: it may contain the database password.
            log.info("mongo.connected")
        return self._client

    @property
    def manifests(self) -> AsyncIOMotorCollection:
        """The manifests collection, opened lazily."""
        return self._ensure_client()[DB_NAME][MANIFESTS_COLLECTION]

    async def close(self) -> None:
        """Close the result."""
        if self._client is not None:
            self._client.close()
            self._client = None
            log.info("mongo.closed")

    @property
    def scorecards(self) -> AsyncIOMotorCollection:
        """The eval scorecards collection, opened lazily."""
        return self._ensure_client()[DB_NAME][SCORECARDS_COLLECTION]     

    @property
    def collections(self):
        """The collections."""
        return self._ensure_client()[DB_NAME][COLLECTIONS_COLLECTION]   

    # ---- Index management ---------------------------------------------------

    async def ensure_indexes(self) -> None:
        """Create the indexes the pipeline relies on. Idempotent."""
        col = self.manifests
        sc = self.scorecards
        await col.create_index([("ingested_at", DESCENDING)])
        await col.create_index([("status", ASCENDING)])
        await col.create_index([("metadata.industry", ASCENDING)])
        await col.create_index([("metadata.doc_type", ASCENDING)])
        await sc.create_index([("created_at", DESCENDING)])
        await sc.create_index([("workflow_name", ASCENDING),
                               ("judge_model", ASCENDING),
                               ("judge_prompt_version", ASCENDING)])
        log.debug("mongo.indexes_ensured")

    # ---- CRUD ---------------------------------------------------------------

    async def upsert_manifest(self, manifest: dict[str, Any]) -> str:
        """Insert or replace a manifest by _id. Returns the _id."""
        if "_id" not in manifest:
            raise ValueError("manifest must include _id (the MinIO key)")
        result = await self.manifests.replace_one(
            {"_id": manifest["_id"]},
            manifest,
            upsert=True,
        )
        action = "inserted" if result.upserted_id else "replaced"
        log.info(
            "mongo.manifest_upserted",
            manifest_id=manifest["_id"],
            action=action,
            chunk_count=manifest.get("chunk_count", 0),
        )
        return manifest["_id"]

    async def get_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        """Fetch a single manifest by _id. Returns None if not found."""
        return await self.manifests.find_one({"_id": manifest_id})

    async def list_manifests(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List manifests, optionally filtered by status, newest first."""
        query: dict[str, Any] = {}
        if status:
            query["status"] = status
        cursor = (
            self.manifests.find(query)
            .sort("ingested_at", DESCENDING)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def mark_status(self, manifest_id: str, status: str) -> None:
        """Update only the status field of an existing manifest."""
        await self.manifests.update_one(
            {"_id": manifest_id},
            {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
        )
        log.info("mongo.manifest_status_updated", manifest_id=manifest_id, status=status)

    async def delete_manifest(self, manifest_id: str) -> bool:
        """Delete a manifest. Returns True if a record was deleted."""
        result = await self.manifests.delete_one({"_id": manifest_id})
        if result.deleted_count:
            log.info("mongo.manifest_deleted", manifest_id=manifest_id)
        return bool(result.deleted_count)


    # ---- Scorecards ---------------------------------------------------------

    async def save_scorecard(self, scorecard: dict[str, Any]) -> str:
        """Insert one eval scorecard. Returns the inserted id as a string.
        Append-only: every run is a new document, so history is the full record."""
        result = await self.scorecards.insert_one(scorecard)
        log.info(
            "mongo.scorecard_saved",
            workflow=scorecard.get("workflow_name"),
            judge_model=scorecard.get("judge_model"),
            judge_version=scorecard.get("judge_prompt_version"),
            overall=scorecard.get("overall_mean"),
        )
        return str(result.inserted_id)

    async def list_scorecards(
        self, workflow_name: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """List scorecards newest-first, optionally for one workflow.
        _id is an ObjectId (not JSON-serializable) so we stringify it."""
        query: dict[str, Any] = {}
        if workflow_name:
            query["workflow_name"] = workflow_name
        cursor = (
            self.scorecards.find(query)
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        cards = await cursor.to_list(length=limit)
        for c in cards:
            c["_id"] = str(c["_id"])   # ObjectId -> str for JSON response
        return cards

# ---------- Manifest builder helper -------------------------------------------


def build_manifest(
    minio_key: str,
    original_filename: str,
    source_format: str,
    byte_size: int,
    chunk_ids: list[str],
    metadata: dict[str, Any] | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """Construct a manifest dict in the canonical shape.

    Centralized so the pipeline and tests build manifests the same way —
    fewer places to forget a field.
    """
    now = datetime.now(timezone.utc)
    return {
        "_id": minio_key,
        "minio_key": minio_key,
        "original_filename": original_filename,
        "source_format": source_format,
        "byte_size": byte_size,
        "chunk_count": len(chunk_ids),
        "chunk_ids": chunk_ids,
        "metadata": metadata or {},
        "status": status,
        "ingested_at": now,
        "updated_at": now,
    }


# ---------- Module-level singleton --------------------------------------------

_default_client: MongoClient | None = None


def get_mongo_client() -> MongoClient:
    """Lazily-constructed module-level client."""
    global _default_client
    if _default_client is None:
        _default_client = MongoClient()
    return _default_client
