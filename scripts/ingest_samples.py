"""Repeatable Phase 2 ingestion: seed Weaviate from samples/.

Each file in samples/ gets per-file metadata from PER_FILE_METADATA below.
Files not in the mapping raise an error — better to fail loudly than to
silently ingest with bogus defaults.

Owns lifecycle: this script constructs the MinIO, Mongo, Weaviate and
Embedder clients, passes them in by injection, and closes them in a
finally block at exit. No global singletons, no resource warnings.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.mongo import MongoClient
from app.ingestion.embedder import Embedder
from app.ingestion.pipeline import ingest_file
from app.observability.logging import get_logger
from app.retrieval.weaviate_client import WeaviateClient
from app.storage.minio_client import ObjectStore

log = get_logger(__name__)


SAMPLES_DIR = Path("samples")


PER_FILE_METADATA: dict[str, dict] = {
    "BYEPFAS_Part B_HE 1st stage RIA and IA BLIND_HORIZON-CL4-2025-05-MATERIALS-51-two-stage.docx": {
        "industry": "research_innovation",
        "doc_type": "proposal",
        "outcome": "failed",
        "framework": "horizon_europe",
        "session_id": "default",
    },
    "FARMLOOPS_proposal.pdf": {
        "industry": "agritech",
        "doc_type": "proposal",
        "outcome": "failed",
        "session_id": "default",
    },
}


async def main():
    if not SAMPLES_DIR.exists():
        raise SystemExit(f"samples dir not found: {SAMPLES_DIR.resolve()}")

    candidates = [
        f for f in SAMPLES_DIR.iterdir()
        if f.is_file() and not f.name.startswith(("_", "."))
    ]
    unknown = [f.name for f in candidates if f.name not in PER_FILE_METADATA]
    if unknown:
        raise SystemExit(
            f"unknown files in samples/ — add to PER_FILE_METADATA or remove:\n"
            + "\n".join(f"  {n}" for n in unknown)
        )

    # ---- Construct all shared resources explicitly ---------------------
    # Doing it here (rather than via module-level get_* singletons) means
    # we have one place to close them at exit. No leaked sockets, no
    # ResourceWarning on shutdown.
    object_store = ObjectStore()
    embedder = Embedder()
    weaviate = WeaviateClient()
    weaviate.connect()
    weaviate.ensure_schema()
    mongo = MongoClient()

    try:
        log.info("seed.start", count=len(candidates))
        total_chunks = 0
        skipped = 0
        for path in candidates:
            metadata = PER_FILE_METADATA[path.name]
            result = await ingest_file(
                path,
                metadata=metadata,
                object_store=object_store,
                embedder=embedder,
                weaviate=weaviate,
                mongo=mongo,
            )
            total_chunks += result.chunk_count
            if result.skipped:
                skipped += 1
            print(
                f"{'SKIP' if result.skipped else 'OK  '}  "
                f"industry={metadata['industry']:25s}  "
                f"doc_type={metadata['doc_type']:12s}  "
                f"chunks={result.chunk_count:4d}  "
                f"{path.name}"
            )

        print(f"\nDone. {len(candidates)} files, {total_chunks} chunks total, {skipped} skipped.")

    finally:
        # ---- Clean shutdown of every client we opened ------------------
        # Order: most-recently-opened first. None of these should ever
        # raise during close; if they do, log and continue so we don't
        # leak the rest.
        for name, closer in [
            ("mongo", getattr(mongo, "close", None)),
            ("weaviate", getattr(weaviate, "close", None)),
            ("embedder", getattr(embedder, "close", None)),
            ("object_store", getattr(object_store, "close", None)),
        ]:
            if closer is None:
                continue
            try:
                result = closer()
                # Some clients are async (motor MongoClient.close is sync,
                # but some have async aclose). Handle both.
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.warning("seed.close_failed", client=name, error=str(e))


if __name__ == "__main__":
    asyncio.run(main())