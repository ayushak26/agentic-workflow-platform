import asyncio
from app.db.mongo import get_mongo_client
from app.ingestion.collections import CollectionConfig, CollectionRegistry

async def main():
    mongo = get_mongo_client()
    try:
        registry = CollectionRegistry(mongo)
        cfg = CollectionConfig(
            collection_id="biomass_monitoring",
            display_name="Biomass Monitoring (HE Cluster 6 / bioeconomy)",
            doc_types=["report", "template"],
            default_industry="bioeconomy",
            description="EEA biomass puzzle + Part B template.",
        )
        await registry.upsert(cfg)
        back = await registry.get("biomass_monitoring")
        print(f"registered: {back.collection_id} doc_types={back.doc_types}")
    finally:
        await mongo.close()

asyncio.run(main())