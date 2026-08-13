"""Starts background ingestion work and recovers unfinished jobs.

No second queue platform: a job is one ``asyncio.Task`` on the same event
loop as the API. That is enough for a single-process deployment; a later
deployment can put the same idempotent ``IngestionJobRunner.run`` behind
Redis-backed durable coordination without changing this module's contract.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.ingestion.indexes import WeaviateSearchIndex
from app.ingestion.jobs import IngestionJobRunner
from app.knowledge.models import IngestionJob, IngestionJobStatus
from app.knowledge.repository import KnowledgeRepository
from app.observability.logging import get_logger
from app.storage.minio_client import ObjectStore

log = get_logger(__name__)


class IngestionCoordinator:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        object_store: ObjectStore,
        embedder: Any,
        weaviate_client: Any,
        redis: Any | None = None,
    ) -> None:
        self.repository = repository
        self.object_store = object_store
        self.embedder = embedder
        self.weaviate_client = weaviate_client
        self.redis = redis
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(self, job: IngestionJob) -> None:
        """Schedule ``job`` to run on the event loop. Returns immediately."""

        task = asyncio.create_task(self._run_safely(job))
        self._tasks[job.ingestion_job_id] = task
        task.add_done_callback(lambda _, job_id=job.ingestion_job_id: self._tasks.pop(job_id, None))

    async def recover(self) -> int:
        """Resubmit every job left non-terminal by an interrupted process.

        ``IngestionJobRunner.run`` re-derives its progress counters from the
        authoritative per-index document mappings before doing anything, so
        resubmitting an in-flight job is safe and idempotent — already
        completed documents are skipped, not redone.
        """

        jobs = await self.repository.list_recoverable_jobs()
        for job in jobs:
            log.info("ingestion.recovering_job", job_id=job.ingestion_job_id, status=job.status.value)
            self.submit(job)
        return len(jobs)

    async def _materialize_sources(self, job: IngestionJob) -> list[Path]:
        """Download this job's already-uploaded sources to a local temp dir.

        ``IngestionJobRunner.run`` operates on local ``Path``s (it needs
        random local access for hashing/parsing); the API layer already
        staged every source into MinIO under ``job.source_inputs`` when the
        job was created.
        """

        directory = Path(tempfile.mkdtemp(prefix="eurskem-ingestion-run-"))
        paths: list[Path] = []
        for source in job.source_inputs:
            destination = directory / source.filename
            data = await asyncio.to_thread(self.object_store.get_bytes, source.storage_key)
            await asyncio.to_thread(destination.write_bytes, data)
            paths.append(destination)
        return paths

    async def _run_safely(self, job: IngestionJob) -> None:
        directory: Path | None = None
        try:
            paths = await self._materialize_sources(job)
            directory = paths[0].parent if paths else None
            index = await self.repository.get_index(job.owner_scope_id, job.target_index_id)
            runner = IngestionJobRunner(
                repository=self.repository,
                object_store=self.object_store,
                embedder=self.embedder,
                search_index=WeaviateSearchIndex(
                    client=self.weaviate_client, collection_name=index.physical_collection
                ),
            )
            await runner.run(job, paths, metadata=job.metadata)
        except Exception:
            log.exception("ingestion.coordinator_job_failed", job_id=job.ingestion_job_id)
            job.status = IngestionJobStatus.FAILED
            job.errors.append({
                "error_type": "CoordinatorError",
                "message": "ingestion failed before per-document stages could start",
            })
            await self.repository.save_ingestion_job(job)
        finally:
            if directory is not None:
                await asyncio.to_thread(shutil.rmtree, directory, True)
