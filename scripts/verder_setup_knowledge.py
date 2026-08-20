"""One-time setup for the Verder Liquids assessment POC's product knowledge.

Creates a real Knowledge Studio collection ("Verder Pump Product Knowledge",
doc_type "spec", with a `product` metadata field) and a real retrieval
profile (hybrid_rerank), then ingests the 7 pump datasheets — each tagged
with its own `product` metadata value so retrieval can be filtered to just
the candidate models a given email actually names (see
workflows/verder_email_intake.yaml's `retrieve_product_knowledge` node).

Vision-augmented parsing isn't a separate ingestion-time flag in this
pipeline (parsing profile choice lives in the Parser Profile system) —
`ingest_file` will use whatever the default parser wired into this
deployment does with a PDF. If that doesn't already do vision-augmented
extraction, the performance-curve graphics in these datasheets won't be
read by the extraction pipeline, only their surrounding text; production
should attach a vision-augmented parser profile to this collection so
those graphics are read too. Documented here rather than silently assumed.

Idempotent: `ingest_file` already skips a file whose manifest shows
"completed"; re-running this script is safe.

Writes the resulting collection_id / retrieval_profile_id to
scripts/verder_knowledge_config.json so scripts/verder_process_emails.py
doesn't need to re-run setup or hardcode ids.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.db.mongo import MongoClient
from app.ingestion.embedder import Embedder
from app.ingestion.pipeline import ingest_file
from app.knowledge.models import ProfileType, ResourceStatus
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.service import KnowledgeService
from app.retrieval.weaviate_client import WeaviateClient
from app.storage.minio_client import ObjectStore

OWNER_SCOPE_ID = "ayush"  # must match the real logged-in user's session_id/username
# (app.api.retrieval._scope: owner_scope_id = user.session_id or user.username) —
# a synthetic scope here means the Studio UI, running as the real user, can
# never see the collection/profile: confirmed live, this was "verder-poc" first.
DATASHEETS_DIR = Path("/Users/ayushkhandelwal/Downloads/Test case")
CONFIG_PATH = Path(__file__).resolve().parent / "verder_knowledge_config.json"

# (filename, product model name)
DATASHEETS = [
    ("04_Datasheet ICP2.pdf", "ICP2"),
    ("04_Datasheet MCP2.pdf", "MCP2"),
    ("04_Datasheet MCP3.pdf", "MCP3"),
    ("04_Datasheet MWP2.pdf", "MWP2"),
    ("04_Datasheet NMS.pdf", "NMS"),
    ("04_Datasheet PHP2.pdf", "PHP2"),
    ("04_Datasheet PRP2.pdf", "PRP2"),
]


async def main() -> None:
    knowledge_client = AsyncIOMotorClient(settings.mongo_uri)
    db = knowledge_client[settings.mongo_db]
    knowledge = KnowledgeService(KnowledgeRepository(db))

    collection = await knowledge.create_collection(
        owner_scope_id=OWNER_SCOPE_ID,
        name="Verder Pump Product Knowledge",
        description="Packo pump series datasheets, for resolving which model an ambiguous customer email means.",
        doc_types=["spec"],
        metadata_schema={"properties": {"product": {"type": "string"}}},
    )
    print(f"collection created: {collection.collection_id}")

    profile = await knowledge.create_profile_version(
        owner_scope_id=OWNER_SCOPE_ID,
        profile_type=ProfileType.RETRIEVAL,
        name="Verder Pump Hybrid Rerank",
        strategy="hybrid_rerank",
        config={
            "strategy": "hybrid_rerank",
            "candidate_count": 20,
            "final_count": 6,
            "alpha": 0.5,
            "fusion_strategy": "relative_score",
            "reranking_enabled": True,
            "compression_enabled": True,
            "query_transform": "none",
            "context_expansion": "none",
            "sentence_window": 2,
        },
    )
    print(f"retrieval profile created: {profile.profile_id} (v{profile.version})")

    # Parser/chunking/embedding profiles per the brief's recommendation
    # (vision-augmented parsing for the datasheets' performance-curve
    # graphics, parent_child chunking) — required to create an IndexVersion,
    # even though this script's own ingest_file() call below (mirroring
    # scripts/ingest_samples.py) doesn't actually route through a parser
    # profile selection mechanism. Documented, not silently assumed: a real
    # Knowledge Studio ingestion (via its own UI/API upload flow, which
    # this repo's low-level ingest_file() pattern bypasses) would apply the
    # chosen parser profile itself; wiring that through is future work.
    parser_profile = await knowledge.create_profile_version(
        owner_scope_id=OWNER_SCOPE_ID, profile_type=ProfileType.PARSER,
        name="Vision-Augmented Datasheet Parser", strategy="vision_augmented",
        config={"strategy": "vision_augmented", "vision_max_pages": 20, "vision_all_pages": True},
    )
    chunking_profile = await knowledge.create_profile_version(
        owner_scope_id=OWNER_SCOPE_ID, profile_type=ProfileType.CHUNKING,
        name="Datasheet Parent/Child Chunking", strategy="parent_child",
        config={"strategy": "parent_child", "target_tokens": 512, "max_tokens": 1024, "overlap_tokens": 64},
    )
    embedding_profile = await knowledge.create_profile_version(
        owner_scope_id=OWNER_SCOPE_ID, profile_type=ProfileType.EMBEDDING,
        name="Default OpenAI Embedding", strategy="dense",
        config={"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536, "batch_size": 64, "data_processing": "external"},
    )

    object_store = ObjectStore()
    embedder = Embedder()
    weaviate = WeaviateClient()
    weaviate.connect()
    weaviate.ensure_schema()
    mongo = MongoClient()

    total_chunks = 0
    try:
        for filename, product in DATASHEETS:
            path = DATASHEETS_DIR / filename
            if not path.exists():
                raise SystemExit(f"datasheet not found: {path}")
            result = await ingest_file(
                path,
                metadata={
                    "collection_id": collection.collection_id,
                    "doc_type": "spec",
                    "product": product,
                    "industry": "industrial_pumps",
                    "session_id": OWNER_SCOPE_ID,
                },
                object_store=object_store,
                embedder=embedder,
                weaviate=weaviate,
                mongo=mongo,
            )
            total_chunks += result.chunk_count
            print(f"{'SKIP' if result.skipped else 'OK  '}  product={product:6s}  chunks={result.chunk_count:4d}  {filename}")
        print(f"\nDone. {len(DATASHEETS)} datasheets, {total_chunks} chunks total.")

        # Point this collection at a real index over what was just ingested.
        # A full Knowledge Studio upload flow builds + activates the index
        # itself as documents land; this script's direct ingest_file() calls
        # (the same low-level entry point scripts/ingest_samples.py uses)
        # bypass that, so the index is built and activated explicitly here
        # instead — same end state (RetrievalService.retrieve() requires an
        # active index), reached by a shorter, POC-appropriate path.
        index = await knowledge.create_index(
            owner_scope_id=OWNER_SCOPE_ID,
            collection_id=collection.collection_id,
            parser_profile_id=parser_profile.profile_id,
            parser_profile_version=parser_profile.version,
            chunking_profile_id=chunking_profile.profile_id,
            chunking_profile_version=chunking_profile.version,
            embedding_profile_id=embedding_profile.profile_id,
            embedding_profile_version=embedding_profile.version,
        )
        # create_index() always starts an index at BUILDING (the real
        # ingestion-triggered flow flips it to READY as chunks land, which
        # this script's direct ingest_file() calls above don't participate
        # in) — fast-forwarded here since the chunks already exist for real.
        index.status = ResourceStatus.READY
        index.physical_collection = "DocumentChunk"
        index.physical_index_id = None
        index.document_count = len(DATASHEETS)
        index.chunk_count = total_chunks
        await knowledge.repository.save_index(index)
        await knowledge.activate_index(
            owner_scope_id=OWNER_SCOPE_ID,
            collection_id=collection.collection_id,
            index_id=index.index_id,
        )
        print(f"index activated: {index.index_id}")
    finally:
        knowledge_client.close()
        for closer in (mongo.close, weaviate.close):
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                print(f"warning: cleanup failed for {closer}: {exc}")

    CONFIG_PATH.write_text(json.dumps({
        "owner_scope_id": OWNER_SCOPE_ID,
        "collection_id": collection.collection_id,
        "retrieval_profile_id": profile.profile_id,
    }, indent=2))
    print(f"\nWrote {CONFIG_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
