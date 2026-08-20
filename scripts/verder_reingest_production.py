"""Re-ingest the 7 Verder pump datasheets into col_01M0D8GYAD5FBWKTF7GCE1Z16F
through the REAL production Knowledge Studio pipeline (IngestionJobRunner),
so the collection's already-linked Parser/Chunking Profiles (vision_augmented,
parent_child) are genuinely exercised and per-file `product` metadata actually
lands in Weaviate — none of which happened under the original legacy
ingest_file() seeding (scripts/verder_setup_knowledge.py).

Creates a NEW IndexVersion (own physical Weaviate class, since the embedding
fingerprint is unchanged: DocumentChunk_6b9a3a1fa395) rather than touching the
existing "DocumentChunk" class — the old index stays intact and reactivatable
if anything here needs to be rolled back. Activates the new index at the end
so the collection (and therefore the unchanged workflow YAML, which only
references collection_id) starts serving the new data.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import weaviate
from motor.motor_asyncio import AsyncIOMotorClient
from weaviate.classes.config import Configure, DataType, Property

from app.config import settings
from app.ingestion.embedder import Embedder
from app.ingestion.indexes import WeaviateSearchIndex
from app.ingestion.jobs import IngestionJobRunner
from app.knowledge.ids import new_resource_id
from app.knowledge.models import IngestionJob, IngestionSourceInput, ProfileType
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.service import KnowledgeService
from app.retrieval.weaviate_client import SCHEMA_PROPERTIES, WeaviateClient
from app.storage.minio_client import ObjectStore, content_hash, knowledge_key_for_path

OWNER_SCOPE_ID = "ayush"
COLLECTION_ID = "col_01M0D8GYAD5FBWKTF7GCE1Z16F"
PARSER_PROFILE_ID = "parserprof_01M0D8GYATG19ZA5SA4SJXE5GS"
CHUNKING_PROFILE_ID = "chkprof_01M0D8GYAW6BQ49G0YXKND1W7Q"
EMBEDDING_PROFILE_ID = "embprof_01M0D8GYAYFARZCB3ZJ88BFQQR"
DATASHEETS_DIR = Path("/Users/ayushkhandelwal/Downloads/Test case")

DATASHEETS = [
    ("04_Datasheet ICP2.pdf", "ICP2"),
    ("04_Datasheet MCP2.pdf", "MCP2"),
    ("04_Datasheet MCP3.pdf", "MCP3"),
    ("04_Datasheet MWP2.pdf", "MWP2"),
    ("04_Datasheet NMS.pdf", "NMS"),
    ("04_Datasheet PHP2.pdf", "PHP2"),
    ("04_Datasheet PRP2.pdf", "PRP2"),
]


def ensure_physical_class_with_product(client: weaviate.WeaviateClient, name: str) -> None:
    """Same as ensure_collection_schema_on, plus a `product` property this
    collection's metadata_schema declares but the base schema doesn't have."""
    if client.collections.exists(name):
        print(f"physical class {name} already exists, leaving schema as-is")
        return
    client.collections.create(
        name=name,
        properties=SCHEMA_PROPERTIES + [
            Property(name="product", data_type=DataType.TEXT, index_filterable=True),
        ],
        vector_config=Configure.Vectors.self_provided(
            vector_index_config=Configure.VectorIndex.hnsw(
                distance_metric=weaviate.classes.config.VectorDistances.COSINE,
            ),
        ),
    )
    print(f"created physical class {name} with product property")


async def main() -> None:
    mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    db = mongo_client[settings.mongo_db]
    repository = KnowledgeRepository(db)
    service = KnowledgeService(repository)

    object_store = ObjectStore()
    embedder = Embedder()
    weaviate_wrapper = WeaviateClient()
    raw_client = weaviate_wrapper.connect()

    try:
        parser = await repository.get_profile(OWNER_SCOPE_ID, PARSER_PROFILE_ID, 1, ProfileType.PARSER)
        chunking = await repository.get_profile(OWNER_SCOPE_ID, CHUNKING_PROFILE_ID, 1, ProfileType.CHUNKING)
        embedding = await repository.get_profile(OWNER_SCOPE_ID, EMBEDDING_PROFILE_ID, 1, ProfileType.EMBEDDING)
        print(f"parser={parser.strategy}  chunking={chunking.strategy}  embedding={embedding.config.get('model')}")

        index = await service.create_index(
            owner_scope_id=OWNER_SCOPE_ID,
            collection_id=COLLECTION_ID,
            parser_profile_id=parser.profile_id, parser_profile_version=parser.version,
            chunking_profile_id=chunking.profile_id, chunking_profile_version=chunking.version,
            embedding_profile_id=embedding.profile_id, embedding_profile_version=embedding.version,
        )
        print(f"created index {index.index_id} v{index.version} -> physical class {index.physical_collection}")

        ensure_physical_class_with_product(raw_client, index.physical_collection)

        runner = IngestionJobRunner(
            repository=repository,
            object_store=object_store,
            embedder=embedder,
            search_index=WeaviateSearchIndex(client=raw_client, collection_name=index.physical_collection),
        )

        total_chunks = 0
        for filename, product in DATASHEETS:
            path = DATASHEETS_DIR / filename
            if not path.exists():
                raise SystemExit(f"datasheet not found: {path}")

            storage_key = knowledge_key_for_path(path, OWNER_SCOPE_ID)
            digest = await asyncio.to_thread(content_hash, path)
            await asyncio.to_thread(
                object_store.put_file, path, storage_key, "application/pdf",
                {"orig_filename": path.name},
            )
            source_input = IngestionSourceInput(
                filename=path.name, storage_key=storage_key,
                mime_type="application/pdf", content_hash=digest,
                byte_size=path.stat().st_size,
            )
            job = IngestionJob(
                ingestion_job_id=new_resource_id("ingestion_job"),
                workspace_id=index.workspace_id,
                owner_scope_id=OWNER_SCOPE_ID,
                collection_id=COLLECTION_ID,
                parser_profile_id=parser.profile_id, parser_profile_version=parser.version,
                chunking_profile_id=chunking.profile_id, chunking_profile_version=chunking.version,
                embedding_profile_id=embedding.profile_id, embedding_profile_version=embedding.version,
                target_index_id=index.index_id,
                documents_total=1,
                source_inputs=[source_input],
                metadata={"product": product, "industry": "industrial_pumps", "doc_type": "spec"},
            )
            await repository.save_ingestion_job(job)
            result = await runner.run(job, [path], metadata=job.metadata)
            total_chunks += result.chunks_created
            print(f"{result.status.value:12s}  product={product:6s}  chunks={result.chunks_created:3d}  "
                  f"failed={result.documents_failed}  {filename}"
                  + (f"  ERRORS={result.errors}" if result.errors else ""))

        print(f"\nDone. {len(DATASHEETS)} datasheets, {total_chunks} chunks total.")

        index = await repository.get_index(OWNER_SCOPE_ID, index.index_id)
        print(f"index status={index.status.value} document_count={index.document_count} chunk_count={index.chunk_count}")
        if index.status.value not in ("ready", "active"):
            raise SystemExit("index is not ready — not activating. Inspect errors above before retrying.")

        await service.activate_index(owner_scope_id=OWNER_SCOPE_ID, collection_id=COLLECTION_ID, index_id=index.index_id)
        print(f"activated index {index.index_id} for collection {COLLECTION_ID}")

    finally:
        mongo_client.close()
        weaviate_wrapper.close()


if __name__ == "__main__":
    asyncio.run(main())
