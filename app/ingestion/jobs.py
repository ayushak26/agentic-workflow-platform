"""Persistent, stage-visible Knowledge Studio ingestion jobs."""
from __future__ import annotations

import asyncio
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.mongo import build_manifest
from app.ingestion.contracts import EmbeddingProvider, SearchIndex, StageRegistry
from app.ingestion.strategies import DEFAULT_STAGE_REGISTRY
from app.knowledge.ids import new_resource_id
from app.knowledge.models import (
    DocumentResource,
    IngestionJob,
    IngestionJobStatus,
    ProfileType,
    ResourceStatus,
    SourceVersionResource,
)
from app.knowledge.repository import KnowledgeRepository
from app.observability.logging import get_logger
from app.observability.metrics import INGESTION_DOCUMENTS, INGESTION_FAILURES, INGESTION_JOBS
from app.retrieval.filters import STANDARD_METADATA_TYPES, metadata_property_name
from app.storage.minio_client import ObjectStore, content_hash, knowledge_key_for_path

log = get_logger(__name__)


class IngestionCancelled(Exception):
    pass


class IngestionJobRunner:
    """Runs without a second queue platform; progress lives in Mongo.

    The API may schedule ``run`` as a background task.  A later deployment can
    place the same idempotent method behind the existing durable coordination
    layer without changing the job/resource contract.
    """

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        object_store: ObjectStore,
        embedder: EmbeddingProvider,
        search_index: SearchIndex,
        stages: StageRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.object_store = object_store
        self.embedder = embedder
        self.search_index = search_index
        self.stages = stages or DEFAULT_STAGE_REGISTRY

    async def _raise_if_cancelled(self, job: IngestionJob) -> None:
        latest = await self.repository.get_ingestion_job(
            job.owner_scope_id, job.ingestion_job_id
        )
        if latest.status == IngestionJobStatus.CANCELLED:
            job.status = IngestionJobStatus.CANCELLED
            job.cancelled_at = latest.cancelled_at or datetime.now(timezone.utc)
            raise IngestionCancelled

    async def run(
        self,
        job: IngestionJob,
        paths: list[Path],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionJob:
        metadata = metadata or {}
        job.documents_total = len(paths)
        # Recovered jobs resume from authoritative per-index mappings instead
        # of incrementing persisted counters a second time.
        job.documents_processed, job.chunks_created = (
            await self.repository.index_document_stats(
                job.owner_scope_id, job.target_index_id
            )
        )
        job.documents_failed = 0
        job.errors = []
        job.started_at = datetime.now(timezone.utc)
        await self.repository.save_ingestion_job(job)

        parser_profile = await self.repository.get_profile(
            job.owner_scope_id,
            job.parser_profile_id,
            job.parser_profile_version,
            ProfileType.PARSER,
        )
        chunking_profile = await self.repository.get_profile(
            job.owner_scope_id,
            job.chunking_profile_id,
            job.chunking_profile_version,
            ProfileType.CHUNKING,
        )
        embedding_profile = await self.repository.get_profile(
            job.owner_scope_id,
            job.embedding_profile_id,
            job.embedding_profile_version,
            ProfileType.EMBEDDING,
        )
        index = await self.repository.get_index(job.owner_scope_id, job.target_index_id)
        collection_resource = await self.repository.get_collection(
            job.owner_scope_id, job.collection_id
        )
        parser = self.stages.get_parser(parser_profile.strategy)
        chunker = self.stages.get_chunker(chunking_profile.strategy)
        enricher = self.stages.enrichers.get("metadata_context")

        for path in paths:
            document_id: str | None = None
            try:
                await self._raise_if_cancelled(job)
                file_hash = await asyncio.to_thread(content_hash, path)
                existing = await self.repository.find_document_by_hash(
                    job.owner_scope_id, job.collection_id, file_hash
                )
                mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                if existing is not None and existing.current_source_version_id:
                    indexed = await self.repository.get_index_document(
                        job.owner_scope_id, index.index_id, existing.document_id
                    )
                    if indexed is not None:
                        await self.repository.save_ingestion_job(job)
                        continue
                    document = existing
                    document_id = document.document_id
                    source_id = document.source_id
                    source_version_id = document.current_source_version_id
                    source = await self.repository.get_source_version(
                        job.owner_scope_id, source_version_id
                    )
                    storage_key = source.storage_key
                    document.status = ResourceStatus.BUILDING
                    document.error = None
                    document.metadata = {**document.metadata, **metadata}
                    await self.repository.save_document(document)
                else:
                    document_id = new_resource_id("document")
                    source_id = new_resource_id("source")
                    source_version_id = new_resource_id("source_version")
                    storage_key = knowledge_key_for_path(path, job.owner_scope_id)
                    document = DocumentResource(
                        document_id=document_id,
                        source_id=source_id,
                        collection_id=job.collection_id,
                        filename=path.name,
                        mime_type=mime_type,
                        source_format=path.suffix.lstrip(".").lower(),
                        content_hash=file_hash,
                        current_source_version_id=source_version_id,
                        metadata=metadata,
                        workspace_id=job.workspace_id,
                        owner_scope_id=job.owner_scope_id,
                        status=ResourceStatus.BUILDING,
                    )
                    await self.repository.save_document(document)
                    job.status = IngestionJobStatus.UPLOADING
                    await self.repository.save_ingestion_job(job)
                    exists = await asyncio.to_thread(
                        self.object_store.object_exists, storage_key
                    )
                    if not exists:
                        await asyncio.to_thread(
                            self.object_store.put_file,
                            path,
                            storage_key,
                            mime_type,
                            {"orig_filename": path.name, "source_version_id": source_version_id},
                        )
                    source = SourceVersionResource(
                        source_version_id=source_version_id,
                        source_id=source_id,
                        document_id=document_id,
                        collection_id=job.collection_id,
                        version=1,
                        filename=path.name,
                        mime_type=mime_type,
                        source_format=path.suffix.lstrip(".").lower(),
                        storage_key=storage_key,
                        content_hash=file_hash,
                        byte_size=path.stat().st_size,
                        metadata=metadata,
                        workspace_id=job.workspace_id,
                        owner_scope_id=job.owner_scope_id,
                    )
                    await self.repository.save_source_version(source)
                job.current_document_id = document_id

                job.status = IngestionJobStatus.PARSING
                await self.repository.save_ingestion_job(job)
                await self._raise_if_cancelled(job)
                parsed = await parser.parse(path, config=parser_profile.config)

                job.status = IngestionJobStatus.CHUNKING
                await self.repository.save_ingestion_job(job)
                await self._raise_if_cancelled(job)
                chunks = await chunker.chunk(
                    parsed,
                    config=chunking_profile.config,
                    chunk_id_prefix=f"{index.index_id}:{source_version_id}",
                )
                if not chunks:
                    raise ValueError("parser/chunker produced no searchable chunks")

                job.status = IngestionJobStatus.ENRICHING
                await self.repository.save_ingestion_job(job)
                if enricher is not None:
                    chunks = await enricher.enrich(
                        chunks,
                        document=parsed,
                        config={"prepend_context": chunking_profile.strategy == "contextual"},
                    )

                job.status = IngestionJobStatus.EMBEDDING
                await self.repository.save_ingestion_job(job)
                await self._raise_if_cancelled(job)
                vectors = await self.embedder.embed([chunk.embedding_content for chunk in chunks])
                if len(vectors) != len(chunks):
                    raise RuntimeError("embedding provider returned the wrong vector count")

                job.status = IngestionJobStatus.INDEXING
                await self.repository.save_ingestion_job(job)
                await self._raise_if_cancelled(job)
                now = datetime.now(timezone.utc)
                objects: list[dict[str, Any]] = []
                for chunk in chunks:
                    unit_index = int(chunk.metadata.get("unit_index", 0))
                    object_payload = {
                            "chunk_id": chunk.chunk_id,
                            "text": chunk.text,
                            "retrieval_content": chunk.embedding_content,
                            "context_content": chunk.context_content or "",
                            "token_count": chunk.token_count,
                            "source_path": path.name,
                            "source_format": parsed.source_format,
                            "unit_index": unit_index,
                            "unit_label": str(chunk.metadata.get("unit_label", "")),
                            "chunk_index": int(chunk.metadata.get("chunk_index", 0)),
                            "industry": str(metadata.get("industry", "")),
                            "doc_type": str(metadata.get("document_type") or metadata.get("doc_type") or "general"),
                            "language": str(metadata.get("language", "")),
                            "session_id": job.owner_scope_id,
                            "workspace_id": job.workspace_id,
                            "collection_id": job.collection_id,
                            "index_id": index.index_id,
                            "document_id": document_id,
                            "source_id": source_id,
                            "source_version_id": source_version_id,
                            "parent_chunk_id": chunk.parent_chunk_id or "",
                            "chunk_role": chunk.chunk_role,
                            "title": chunk.title or path.name,
                            "section": chunk.section or str(chunk.metadata.get("unit_label", "")),
                            "page": unit_index + 1 if parsed.source_format == "pdf" else 0,
                            "parser_profile_id": parser_profile.profile_id,
                            "chunking_profile_id": chunking_profile.profile_id,
                            "embedding_profile_id": embedding_profile.profile_id,
                            "ingested_at": now,
                        }
                    for key, value in metadata.items():
                        property_name = metadata_property_name(str(key))
                        definition = collection_resource.metadata_schema.get(
                            "properties", collection_resource.metadata_schema
                        ).get(str(key), {})
                        value_type = (
                            definition.get("type")
                            if isinstance(definition, dict)
                            else definition
                        ) or STANDARD_METADATA_TYPES.get(str(key))
                        if value_type in {"date", "datetime"} and isinstance(value, str):
                            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        object_payload[property_name] = value
                    objects.append(object_payload)
                inserted = await self.search_index.index(objects, vectors)
                if inserted != len(objects):
                    raise RuntimeError(f"only {inserted}/{len(objects)} chunks were indexed")

                document.status = ResourceStatus.READY
                document.source_format = parsed.source_format
                await self.repository.save_document(document)
                chunk_ids = [chunk.chunk_id for chunk in chunks]
                await self.repository.save_index_document(
                    owner_scope_id=job.owner_scope_id,
                    index_id=index.index_id,
                    document_id=document_id,
                    source_version_id=source_version_id,
                    ingestion_job_id=job.ingestion_job_id,
                    chunk_ids=chunk_ids,
                )
                # Preserve the existing manifest provenance surface.
                manifest = build_manifest(
                    minio_key=storage_key,
                    original_filename=path.name,
                    source_format=parsed.source_format,
                    byte_size=path.stat().st_size,
                    chunk_ids=chunk_ids,
                    metadata={
                        **metadata,
                        "owner_scope_id": job.owner_scope_id,
                        "collection_id": job.collection_id,
                        "document_id": document_id,
                        "source_id": source_id,
                        "source_version_id": source_version_id,
                        "ingestion_job_id": job.ingestion_job_id,
                        "index_id": index.index_id,
                    },
                )
                index_history = {
                    "index_id": index.index_id,
                    "ingestion_job_id": job.ingestion_job_id,
                    "chunk_ids": chunk_ids,
                    "profile_versions": {
                        "parser": parser_profile.version,
                        "chunking": chunking_profile.version,
                        "embedding": embedding_profile.version,
                    },
                }
                await self.repository.record_manifest_index_history(
                    storage_key=storage_key,
                    manifest=manifest,
                    index_history=index_history,
                )
                job.documents_processed += 1
                job.chunks_created += inserted
                INGESTION_DOCUMENTS.labels(status="completed").inc()
            except IngestionCancelled:
                break
            except Exception as exc:
                job.documents_failed += 1
                job.errors.append(
                    {
                        "document_id": document_id,
                        "filename": path.name,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                )
                INGESTION_DOCUMENTS.labels(status="failed").inc()
                INGESTION_FAILURES.labels(stage=job.status.value).inc()
                if document_id:
                    try:
                        document = await self.repository.get_document(job.owner_scope_id, document_id)
                        document.status = ResourceStatus.FAILED
                        document.error = str(exc)[:1000]
                        await self.repository.save_document(document)
                    except Exception:
                        log.exception("ingestion.document_failure_record_failed")
                log.exception("ingestion.document_failed", job_id=job.ingestion_job_id)
            finally:
                await self.repository.save_ingestion_job(job)

        job.current_document_id = None
        job.completed_at = datetime.now(timezone.utc)
        if job.status == IngestionJobStatus.CANCELLED:
            pass
        elif job.documents_failed == 0:
            job.status = IngestionJobStatus.COMPLETED
        elif job.documents_processed:
            job.status = IngestionJobStatus.PARTIALLY_COMPLETED
        else:
            job.status = IngestionJobStatus.FAILED
        index.document_count, index.chunk_count = await self.repository.index_document_stats(
            job.owner_scope_id, index.index_id
        )
        index_is_usable = (
            job.status in {
                IngestionJobStatus.COMPLETED,
                IngestionJobStatus.PARTIALLY_COMPLETED,
            }
            and bool(index.document_count)
        )
        index.status = ResourceStatus.READY if index_is_usable else ResourceStatus.FAILED
        await self.repository.save_index(index)
        if index_is_usable:
            collection = await self.repository.get_collection(
                job.owner_scope_id, job.collection_id
            )
            if collection.active_index_id is None:
                index.status = ResourceStatus.ACTIVE
                index.activated_at = datetime.now(timezone.utc)
                await self.repository.save_index(index)
                collection.active_index_id = index.index_id
                collection.status = ResourceStatus.READY
            if collection.active_index_id == index.index_id:
                collection.document_count = index.document_count
                collection.chunk_count = index.chunk_count
            await self.repository.save_collection(collection)
        await self.repository.save_ingestion_job(job)
        INGESTION_JOBS.labels(status=job.status.value).inc()
        return job
