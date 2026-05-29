"""End-to-end document ingestion pipeline.

Orchestrates: extract → chunk → embed → store. Idempotent via content-addressed
MinIO keys and Mongo manifest status tracking. A re-ingest of the same file is
a no-op; a re-ingest after a partial failure recovers cleanly.

Concurrency: this function is async. Mongo (motor) and the embedder
(AsyncOpenAI) are natively async. Weaviate's v4 client is sync, so writes
to Weaviate run in a thread executor via asyncio.to_thread to avoid blocking
the event loop. Same pattern for MinIO uploads (boto3 is sync).

Ingest one document end-to-end. Idempotent.

`metadata` lets the caller tag the document with application-level fields
(industry, doc_type, session_id, etc.) that flow into Weaviate as per-chunk
metadata and into the manifest as document-level metadata. Defaults to {}.

If `metadata["session_id"]` is omitted, chunks are written under the
"default" session — fine for dev/demo. Real workflows must pass an
explicit session_id; Phase 11's isolation verifier enforces this.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db.mongo import MongoClient, build_manifest, get_mongo_client
from app.ingestion.chunker import Chunk, ChunkConfig, chunk_document
from app.ingestion.embedder import Embedder, get_embedder
from app.ingestion.extractor import get_extractor
from app.observability.logging import get_logger
from app.retrieval.weaviate_client import WeaviateClient, get_weaviate_client
from app.storage.minio_client import ObjectStore, get_object_store, key_for_path
from datetime import datetime, timezone

log = get_logger(__name__)


# ---------- Result shape ------------------------------------------------------


@dataclass
class IngestionResult:
    """What `ingest_file` returns. Mirrors the manifest stored in Mongo."""

    minio_key: str
    chunk_count: int
    chunk_ids: list[str]
    status: str
    skipped: bool = False  # True if the file was already ingested


# ---------- Main entry point --------------------------------------------------


async def ingest_file(
    path: Path,
    metadata: dict[str, Any] | None = None,
    chunk_config: ChunkConfig | None = None,
    # Dependency injection slots — defaults pull from module singletons
    object_store: ObjectStore | None = None,
    embedder: Embedder | None = None,
    weaviate: WeaviateClient | None = None,
    mongo: MongoClient | None = None,
) -> IngestionResult:
    """Ingest one document end-to-end. Idempotent.

    `metadata` lets the caller tag the document with application-level fields
    (industry, doc_type, etc.) that flow into Weaviate as per-chunk metadata
    and into the manifest as document-level metadata. Defaults to {}.
    """
    metadata = metadata or {}
    object_store = object_store or get_object_store()
    embedder = embedder or get_embedder()
    weaviate = weaviate or get_weaviate_client()
    mongo = mongo or get_mongo_client()

    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")

    minio_key = key_for_path(path)
    log.info("pipeline.start", path=str(path), minio_key=minio_key)

    # ---- Step 1: idempotency check ------------------------------------------
    existing = await mongo.get_manifest(minio_key)
    if existing and existing.get("status") == "completed":
        log.info(
            "pipeline.skip_already_ingested",
            minio_key=minio_key,
            chunk_count=existing["chunk_count"],
        )
        return IngestionResult(
            minio_key=minio_key,
            chunk_count=existing["chunk_count"],
            chunk_ids=existing["chunk_ids"],
            status="completed",
            skipped=True,
        )

    # ---- Step 2: write in_progress manifest as a lock -----------------------
    in_progress = build_manifest(
        minio_key=minio_key,
        original_filename=path.name,
        source_format=path.suffix.lstrip(".").lower(),
        byte_size=path.stat().st_size,
        chunk_ids=[],
        metadata=metadata,
        status="in_progress",
    )
    await mongo.upsert_manifest(in_progress)

    try:
        # ---- Step 3: upload to MinIO (sync boto3 → to_thread) ----------------
        await asyncio.to_thread(
            object_store.put_file,
            path,
            key=minio_key,
            extra_metadata={"orig_filename": path.name},
        )

        # ---- Step 4: extract ------------------------------------------------
        extractor = get_extractor(path)
        doc = await asyncio.to_thread(extractor.extract, path)

        # Layer in caller-provided metadata so chunks inherit it
        doc.metadata.update({f"app_{k}": str(v) for k, v in metadata.items()})

        # ---- Step 5: chunk --------------------------------------------------
        chunks: list[Chunk] = chunk_document(doc, config=chunk_config)
        if not chunks:
            log.warning("pipeline.no_chunks", minio_key=minio_key)
            await mongo.mark_status(minio_key, "failed")
            return IngestionResult(
                minio_key=minio_key,
                chunk_count=0,
                chunk_ids=[],
                status="failed",
            )

        # ---- Step 6: embed --------------------------------------------------
        texts = [c.text for c in chunks]
        vectors = await embedder.embed(texts)
        assert len(vectors) == len(chunks)

        # ---- Step 7: prepare Weaviate payloads ------------------------------
        # Each chunk's metadata + the application-level metadata + text
        weaviate_objects: list[dict[str, Any]] = []
        for c in chunks:
            obj = {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "token_count": c.token_count,
                "source_path": str(c.metadata.get("source_path", "")),
                "source_format": str(c.metadata.get("source_format", "")),
                "unit_index": int(c.metadata.get("unit_index", 0)),
                "unit_label": str(c.metadata.get("unit_label", "")),
                "chunk_index": int(c.metadata.get("chunk_index", 0)),
                # Application metadata
                "industry": str(metadata.get("industry", "")),
                "doc_type": str(metadata.get("doc_type", "")),
                "language": str(c.metadata.get("doc_language", "")),
                "session_id": str(metadata.get("session_id", "default")),
                "collection_id": str(metadata.get("collection_id", "default")),
                "ingested_at": datetime.now(timezone.utc)
            }
            weaviate_objects.append(obj)

        # ---- Step 8: upsert into Weaviate (sync v4 client → to_thread) ------
        inserted = await asyncio.to_thread(
            weaviate.upsert_chunks, weaviate_objects, vectors
        )

        # ---- Step 9: finalize manifest --------------------------------------
        final_manifest = build_manifest(
            minio_key=minio_key,
            original_filename=path.name,
            source_format=doc.source_format,
            byte_size=path.stat().st_size,
            chunk_ids=[c.chunk_id for c in chunks],
            metadata=metadata,
            status="completed",
        )
        await mongo.upsert_manifest(final_manifest)

        log.info(
            "pipeline.done",
            minio_key=minio_key,
            chunk_count=inserted,
            source_format=doc.source_format,
        )

        return IngestionResult(
            minio_key=minio_key,
            chunk_count=inserted,
            chunk_ids=[c.chunk_id for c in chunks],
            status="completed",
        )

    except Exception as e:
        log.error(
            "pipeline.failed",
            minio_key=minio_key,
            error_type=type(e).__name__,
            error=str(e),
        )
        await mongo.mark_status(minio_key, "failed")
        raise