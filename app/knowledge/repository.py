"""Owner-scoped Mongo persistence for Knowledge Studio resources.

Every read and write takes ``owner_scope_id`` and folds it into the query or
the stored document. Nothing in this module trusts a caller-supplied scope
that hasn't already been pinned from the authenticated user — that pinning
happens once, in the API layer's ``_scope(user)`` helpers.

Profiles are append-only: :meth:`save_profile` always inserts a new
``(profile_id, version)`` row rather than mutating history, matching the
Knowledge Studio requirement that updating a profile creates a new version.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.mongo import (
    INGESTION_JOBS_COLLECTION,
    KNOWLEDGE_COLLECTIONS_COLLECTION,
    KNOWLEDGE_DOCUMENTS_COLLECTION,
    KNOWLEDGE_INDEX_DOCUMENTS_COLLECTION,
    KNOWLEDGE_INDEXES_COLLECTION,
    KNOWLEDGE_PROFILES_COLLECTION,
    KNOWLEDGE_SOURCE_VERSIONS_COLLECTION,
    RAG_AGENTS_COLLECTION,
    RETRIEVAL_TRACES_COLLECTION,
)
from app.knowledge.models import (
    CollectionResource,
    DocumentResource,
    IndexVersion,
    IngestionJob,
    ProfileType,
    ProfileVersion,
    RAGAgentDefinition,
    RetrievalTrace,
    SourceVersionResource,
)


class ResourceNotFoundError(LookupError):
    """A scoped resource lookup found nothing for this owner."""


class ResourceConflictError(ValueError):
    """A resource exists but is not in a state the caller may act on."""


def _scope(owner_scope_id: str, **extra: Any) -> dict[str, Any]:
    """Internal helper for the scope step.

    Args:
        owner_scope_id (str): The owner scope id.
        **extra (Any): The extra.

    Returns:
        dict[str, Any]: The result.
    """
    return {"owner_scope_id": owner_scope_id, **extra}


def _strip(document: dict[str, Any]) -> dict[str, Any]:
    """Internal helper for the strip step.

    Args:
        document (dict[str, Any]): Document.

    Returns:
        dict[str, Any]: The result.
    """
    document.pop("_id", None)
    return document


class KnowledgeRepository:
    """One instance shared across the app via ``services["knowledge_repository"]``."""

    def __init__(self, db: Any):
        """Initialize the KnowledgeRepository.

        Args:
            db (Any): Mongo database handle.
        """
        self.db = db

    # -- collection helpers --------------------------------------------------
    @property
    def _collections(self):
        """The collections."""
        return self.db[KNOWLEDGE_COLLECTIONS_COLLECTION]

    @property
    def _documents(self):
        """The documents."""
        return self.db[KNOWLEDGE_DOCUMENTS_COLLECTION]

    @property
    def _source_versions(self):
        """The source versions."""
        return self.db[KNOWLEDGE_SOURCE_VERSIONS_COLLECTION]

    @property
    def _profiles(self):
        """The profiles."""
        return self.db[KNOWLEDGE_PROFILES_COLLECTION]

    @property
    def _indexes(self):
        """The indexes."""
        return self.db[KNOWLEDGE_INDEXES_COLLECTION]

    @property
    def _index_documents(self):
        """The index documents."""
        return self.db[KNOWLEDGE_INDEX_DOCUMENTS_COLLECTION]

    @property
    def _ingestion_jobs(self):
        """The ingestion jobs."""
        return self.db[INGESTION_JOBS_COLLECTION]

    @property
    def _rag_agents(self):
        """The rag agents."""
        return self.db[RAG_AGENTS_COLLECTION]

    @property
    def _traces(self):
        """The traces."""
        return self.db[RETRIEVAL_TRACES_COLLECTION]

    async def ensure_indexes(self) -> None:
        """Ensure the indexes."""
        await self._collections.create_index([("owner_scope_id", 1), ("collection_id", 1)], unique=True)
        await self._documents.create_index([("owner_scope_id", 1), ("document_id", 1)], unique=True)
        await self._documents.create_index([("owner_scope_id", 1), ("collection_id", 1), ("content_hash", 1)])
        await self._source_versions.create_index([("owner_scope_id", 1), ("source_version_id", 1)], unique=True)
        await self._profiles.create_index(
            [("owner_scope_id", 1), ("profile_id", 1), ("version", 1)], unique=True
        )
        await self._indexes.create_index([("owner_scope_id", 1), ("index_id", 1)], unique=True)
        await self._indexes.create_index([("owner_scope_id", 1), ("collection_id", 1)])
        await self._index_documents.create_index(
            [("owner_scope_id", 1), ("index_id", 1), ("document_id", 1)], unique=True
        )
        await self._ingestion_jobs.create_index([("owner_scope_id", 1), ("ingestion_job_id", 1)], unique=True)
        await self._rag_agents.create_index([("owner_scope_id", 1), ("rag_agent_id", 1)], unique=True)
        await self._traces.create_index([("owner_scope_id", 1), ("retrieval_request_id", 1)], unique=True)
        await self._traces.create_index([("expires_at", 1)], expireAfterSeconds=0)

    # -- collections ----------------------------------------------------
    async def create_collection(self, resource: CollectionResource) -> CollectionResource:
        """Create the collection.

        Args:
            resource (CollectionResource): The resource.

        Returns:
            CollectionResource: The collection.
        """
        await self._collections.insert_one(resource.model_dump(mode="json"))
        return resource

    async def save_collection(self, resource: CollectionResource) -> CollectionResource:
        """Save the collection.

        Args:
            resource (CollectionResource): The resource.

        Returns:
            CollectionResource: The collection.
        """
        resource.updated_at = datetime.now(timezone.utc)
        await self._collections.replace_one(
            _scope(resource.owner_scope_id, collection_id=resource.collection_id),
            resource.model_dump(mode="json"),
            upsert=True,
        )
        return resource

    async def get_collection(self, owner_scope_id: str, collection_id: str) -> CollectionResource:
        """Return the collection.

        Args:
            owner_scope_id (str): The owner scope id.
            collection_id (str): Knowledge collection identifier.

        Returns:
            CollectionResource: The collection.
        """
        doc = await self._collections.find_one(_scope(owner_scope_id, collection_id=collection_id))
        if doc is None:
            raise ResourceNotFoundError(f"collection {collection_id!r} was not found")
        return CollectionResource.model_validate(_strip(doc))

    async def list_collections(self, owner_scope_id: str) -> list[CollectionResource]:
        """List the collections.

        Args:
            owner_scope_id (str): The owner scope id.

        Returns:
            list[CollectionResource]: The collections.
        """
        cursor = self._collections.find({"owner_scope_id": owner_scope_id})
        return [CollectionResource.model_validate(_strip(doc)) async for doc in cursor]

    # -- documents / source versions -------------------------------------
    async def save_document(self, resource: DocumentResource) -> DocumentResource:
        """Save the document.

        Args:
            resource (DocumentResource): The resource.

        Returns:
            DocumentResource: The document.
        """
        resource.updated_at = datetime.now(timezone.utc)
        await self._documents.replace_one(
            _scope(resource.owner_scope_id, document_id=resource.document_id),
            resource.model_dump(mode="json"),
            upsert=True,
        )
        return resource

    async def get_document(self, owner_scope_id: str, document_id: str) -> DocumentResource:
        """Return the document.

        Args:
            owner_scope_id (str): The owner scope id.
            document_id (str): The document id.

        Returns:
            DocumentResource: The document.
        """
        doc = await self._documents.find_one(_scope(owner_scope_id, document_id=document_id))
        if doc is None:
            raise ResourceNotFoundError(f"document {document_id!r} was not found")
        return DocumentResource.model_validate(_strip(doc))

    async def find_document_by_hash(
        self, owner_scope_id: str, collection_id: str, content_hash: str
    ) -> DocumentResource | None:
        """Find the document by hash.

        Args:
            owner_scope_id (str): The owner scope id.
            collection_id (str): Knowledge collection identifier.
            content_hash (str): The content hash.

        Returns:
            DocumentResource | None: The document by hash.
        """
        doc = await self._documents.find_one(
            _scope(owner_scope_id, collection_id=collection_id, content_hash=content_hash)
        )
        return DocumentResource.model_validate(_strip(doc)) if doc else None

    async def list_documents(self, owner_scope_id: str, collection_id: str) -> list[DocumentResource]:
        """List the documents.

        Args:
            owner_scope_id (str): The owner scope id.
            collection_id (str): Knowledge collection identifier.

        Returns:
            list[DocumentResource]: The documents.
        """
        cursor = self._documents.find(_scope(owner_scope_id, collection_id=collection_id))
        return [DocumentResource.model_validate(_strip(doc)) async for doc in cursor]

    async def save_source_version(self, resource: SourceVersionResource) -> SourceVersionResource:
        """Save the source version.

        Args:
            resource (SourceVersionResource): The resource.

        Returns:
            SourceVersionResource: The source version.
        """
        await self._source_versions.replace_one(
            _scope(resource.owner_scope_id, source_version_id=resource.source_version_id),
            resource.model_dump(mode="json"),
            upsert=True,
        )
        return resource

    async def get_source_version(
        self, owner_scope_id: str, source_version_id: str
    ) -> SourceVersionResource:
        """Return the source version.

        Args:
            owner_scope_id (str): The owner scope id.
            source_version_id (str): The source version id.

        Returns:
            SourceVersionResource: The source version.
        """
        doc = await self._source_versions.find_one(
            _scope(owner_scope_id, source_version_id=source_version_id)
        )
        if doc is None:
            raise ResourceNotFoundError(f"source version {source_version_id!r} was not found")
        return SourceVersionResource.model_validate(_strip(doc))

    # -- profiles (append-only versions) ---------------------------------
    async def next_profile_version(self, owner_scope_id: str, profile_id: str) -> int:
        """Compute the next profile version.

        Args:
            owner_scope_id (str): The owner scope id.
            profile_id (str): The profile id.

        Returns:
            int: The profile version.
        """
        latest = await self._profiles.find_one(
            _scope(owner_scope_id, profile_id=profile_id), sort=[("version", -1)]
        )
        return (latest["version"] + 1) if latest else 1

    async def save_profile(self, profile: ProfileVersion) -> ProfileVersion:
        """Save the profile.

        Args:
            profile (ProfileVersion): The profile.

        Returns:
            ProfileVersion: The profile.
        """
        await self._profiles.insert_one(profile.model_dump(mode="json"))
        return profile

    async def get_profile(
        self,
        owner_scope_id: str,
        profile_id: str,
        version: int | None = None,
        expected_type: ProfileType | None = None,
    ) -> ProfileVersion:
        """Return the profile.

        Args:
            owner_scope_id (str): The owner scope id.
            profile_id (str): The profile id.
            version (int | None): Version identifier (optional, default None).
            expected_type (ProfileType | None): The expected type (optional, default None).

        Returns:
            ProfileVersion: The profile.
        """
        query = _scope(owner_scope_id, profile_id=profile_id)
        if version is not None:
            query["version"] = version
        doc = await self._profiles.find_one(query, sort=[("version", -1)])
        if doc is None:
            raise ResourceNotFoundError(f"profile {profile_id!r} was not found")
        profile = ProfileVersion.model_validate(_strip(doc))
        if expected_type is not None and profile.profile_type != expected_type:
            raise ResourceNotFoundError(
                f"profile {profile_id!r} is a {profile.profile_type.value} profile, "
                f"not a {expected_type.value} profile"
            )
        return profile

    async def list_profiles(
        self, owner_scope_id: str, profile_type: ProfileType | None = None
    ) -> list[ProfileVersion]:
        """List the profiles.

        Args:
            owner_scope_id (str): The owner scope id.
            profile_type (ProfileType | None): The profile type (optional, default None).

        Returns:
            list[ProfileVersion]: The profiles.
        """
        query: dict[str, Any] = {"owner_scope_id": owner_scope_id}
        if profile_type is not None:
            query["profile_type"] = profile_type.value
        latest_by_id: dict[str, ProfileVersion] = {}
        async for doc in self._profiles.find(query).sort("version", 1):
            profile = ProfileVersion.model_validate(_strip(doc))
            latest_by_id[profile.profile_id] = profile
        return list(latest_by_id.values())

    # -- indexes ----------------------------------------------------------
    async def save_index(self, index: IndexVersion) -> IndexVersion:
        """Save the index.

        Args:
            index (IndexVersion): Index.

        Returns:
            IndexVersion: The index.
        """
        index.updated_at = datetime.now(timezone.utc)
        await self._indexes.replace_one(
            _scope(index.owner_scope_id, index_id=index.index_id),
            index.model_dump(mode="json"),
            upsert=True,
        )
        return index

    async def get_index(self, owner_scope_id: str, index_id: str) -> IndexVersion:
        """Return the index.

        Args:
            owner_scope_id (str): The owner scope id.
            index_id (str): The index id.

        Returns:
            IndexVersion: The index.
        """
        doc = await self._indexes.find_one(_scope(owner_scope_id, index_id=index_id))
        if doc is None:
            raise ResourceNotFoundError(f"index {index_id!r} was not found")
        return IndexVersion.model_validate(_strip(doc))

    async def list_indexes(self, owner_scope_id: str, collection_id: str) -> list[IndexVersion]:
        """List the indexes.

        Args:
            owner_scope_id (str): The owner scope id.
            collection_id (str): Knowledge collection identifier.

        Returns:
            list[IndexVersion]: The indexes.
        """
        cursor = self._indexes.find(_scope(owner_scope_id, collection_id=collection_id))
        return [IndexVersion.model_validate(_strip(doc)) async for doc in cursor]

    # -- per-index document outcomes --------------------------------------
    async def save_index_document(
        self,
        *,
        owner_scope_id: str,
        index_id: str,
        document_id: str,
        source_version_id: str,
        ingestion_job_id: str,
        chunk_ids: list[str],
    ) -> None:
        """Save the index document.

        Args:
            owner_scope_id (str): The owner scope id.
            index_id (str): The index id.
            document_id (str): The document id.
            source_version_id (str): The source version id.
            ingestion_job_id (str): The ingestion job id.
            chunk_ids (list[str]): Weaviate chunk identifiers.
        """
        await self._index_documents.replace_one(
            _scope(owner_scope_id, index_id=index_id, document_id=document_id),
            {
                "owner_scope_id": owner_scope_id,
                "index_id": index_id,
                "document_id": document_id,
                "source_version_id": source_version_id,
                "ingestion_job_id": ingestion_job_id,
                "chunk_ids": chunk_ids,
                "chunk_count": len(chunk_ids),
                "indexed_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )

    async def get_index_document(
        self, owner_scope_id: str, index_id: str, document_id: str
    ) -> dict[str, Any] | None:
        """Return the index document.

        Args:
            owner_scope_id (str): The owner scope id.
            index_id (str): The index id.
            document_id (str): The document id.

        Returns:
            dict[str, Any] | None: The index document.
        """
        doc = await self._index_documents.find_one(
            _scope(owner_scope_id, index_id=index_id, document_id=document_id)
        )
        return _strip(doc) if doc else None

    async def index_document_stats(self, owner_scope_id: str, index_id: str) -> tuple[int, int]:
        """Return ``(document_count, chunk_count)`` from authoritative mappings.

        Recovered ingestion jobs read counters from here rather than trusting
        a persisted counter that might have stopped mid-write.
        """

        document_count = 0
        chunk_count = 0
        async for doc in self._index_documents.find(_scope(owner_scope_id, index_id=index_id)):
            document_count += 1
            chunk_count += int(doc.get("chunk_count") or len(doc.get("chunk_ids") or []))
        return document_count, chunk_count

    # -- ingestion jobs -----------------------------------------------------
    async def save_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        """Save the ingestion job.

        Args:
            job (IngestionJob): The job.

        Returns:
            IngestionJob: The ingestion job.
        """
        job.updated_at = datetime.now(timezone.utc)
        await self._ingestion_jobs.replace_one(
            _scope(job.owner_scope_id, ingestion_job_id=job.ingestion_job_id),
            job.model_dump(mode="json"),
            upsert=True,
        )
        return job

    async def get_ingestion_job(self, owner_scope_id: str, ingestion_job_id: str) -> IngestionJob:
        """Return the ingestion job.

        Args:
            owner_scope_id (str): The owner scope id.
            ingestion_job_id (str): The ingestion job id.

        Returns:
            IngestionJob: The ingestion job.
        """
        doc = await self._ingestion_jobs.find_one(
            _scope(owner_scope_id, ingestion_job_id=ingestion_job_id)
        )
        if doc is None:
            raise ResourceNotFoundError(f"ingestion job {ingestion_job_id!r} was not found")
        return IngestionJob.model_validate(_strip(doc))

    async def list_ingestion_jobs(
        self, owner_scope_id: str, collection_id: str | None = None
    ) -> list[IngestionJob]:
        """List the ingestion jobs.

        Args:
            owner_scope_id (str): The owner scope id.
            collection_id (str | None): Knowledge collection identifier (optional, default None).

        Returns:
            list[IngestionJob]: The ingestion jobs.
        """
        query = {"owner_scope_id": owner_scope_id}
        if collection_id is not None:
            query["collection_id"] = collection_id
        cursor = self._ingestion_jobs.find(query).sort("created_at", -1)
        return [IngestionJob.model_validate(_strip(doc)) async for doc in cursor]

    async def list_recoverable_jobs(self) -> list[IngestionJob]:
        """Non-terminal jobs across every owner, for startup recovery only.

        This is the one place a query intentionally spans owner scope — a
        maintenance sweep run once at process startup, never reachable from
        an API route. Every other repository method stays owner-pinned.
        """

        terminal = {"completed", "partially_completed", "failed", "cancelled"}
        cursor = self._ingestion_jobs.find({"status": {"$nin": list(terminal)}})
        return [IngestionJob.model_validate(_strip(doc)) async for doc in cursor]

    # -- RAG agents ---------------------------------------------------------
    async def save_rag_agent(self, agent: RAGAgentDefinition) -> RAGAgentDefinition:
        """Save the rag agent.

        Args:
            agent (RAGAgentDefinition): The agent.

        Returns:
            RAGAgentDefinition: The rag agent.
        """
        agent.updated_at = datetime.now(timezone.utc)
        await self._rag_agents.replace_one(
            _scope(agent.owner_scope_id, rag_agent_id=agent.rag_agent_id),
            agent.model_dump(mode="json"),
            upsert=True,
        )
        return agent

    async def get_rag_agent(self, owner_scope_id: str, rag_agent_id: str) -> RAGAgentDefinition:
        """Return the rag agent.

        Args:
            owner_scope_id (str): The owner scope id.
            rag_agent_id (str): The rag agent id.

        Returns:
            RAGAgentDefinition: The rag agent.
        """
        doc = await self._rag_agents.find_one(_scope(owner_scope_id, rag_agent_id=rag_agent_id))
        if doc is None:
            raise ResourceNotFoundError(f"RAG agent {rag_agent_id!r} was not found")
        return RAGAgentDefinition.model_validate(_strip(doc))

    async def list_rag_agents(
        self, owner_scope_id: str, search: str | None = None
    ) -> list[RAGAgentDefinition]:
        """List the rag agents.

        Args:
            owner_scope_id (str): The owner scope id.
            search (str | None): The search (optional, default None).

        Returns:
            list[RAGAgentDefinition]: The rag agents.
        """
        cursor = self._rag_agents.find({"owner_scope_id": owner_scope_id})
        agents = [RAGAgentDefinition.model_validate(_strip(doc)) async for doc in cursor]
        needle = search.strip().casefold() if search else ""
        if not needle:
            return agents
        return [agent for agent in agents if needle in agent.name.casefold()]

    # -- retrieval traces -----------------------------------------------------
    async def save_trace(self, trace: RetrievalTrace) -> RetrievalTrace:
        """Save the trace.

        Args:
            trace (RetrievalTrace): The trace.

        Returns:
            RetrievalTrace: The trace.
        """
        await self._traces.replace_one(
            _scope(trace.owner_scope_id, retrieval_request_id=trace.retrieval_request_id),
            trace.model_dump(mode="json"),
            upsert=True,
        )
        return trace

    async def get_trace(self, owner_scope_id: str, retrieval_request_id: str) -> RetrievalTrace:
        """Return the trace.

        Args:
            owner_scope_id (str): The owner scope id.
            retrieval_request_id (str): The retrieval request id.

        Returns:
            RetrievalTrace: The trace.
        """
        doc = await self._traces.find_one(
            _scope(owner_scope_id, retrieval_request_id=retrieval_request_id)
        )
        if doc is None:
            raise ResourceNotFoundError(f"retrieval trace {retrieval_request_id!r} was not found")
        return RetrievalTrace.model_validate(_strip(doc))

    async def list_traces(self, owner_scope_id: str, limit: int = 100) -> list[RetrievalTrace]:
        """List the traces.

        Args:
            owner_scope_id (str): The owner scope id.
            limit (int): Maximum number of items to return (optional, default 100).

        Returns:
            list[RetrievalTrace]: The traces.
        """
        cursor = self._traces.find({"owner_scope_id": owner_scope_id}).sort("created_at", -1).limit(limit)
        return [RetrievalTrace.model_validate(_strip(doc)) async for doc in cursor]

    # -- manifest provenance (legacy surface) --------------------------------
    async def record_manifest_index_history(
        self, *, storage_key: str, manifest: dict[str, Any], index_history: dict[str, Any]
    ) -> None:
        """Upsert the legacy manifest while appending this index's history.

        Keeps the pre-existing `manifests` provenance collection as the one
        place ingestion writes to it, rather than callers reaching into
        ``self.db["manifests"]`` directly.
        """

        manifest = dict(manifest)
        manifest.pop("_id", None)
        await self.db["manifests"].update_one(
            {"_id": storage_key},
            {"$set": manifest, "$addToSet": {"index_history": index_history}},
            upsert=True,
        )
