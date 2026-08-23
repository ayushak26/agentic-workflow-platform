# scripts/seed_collections.py
"""Seed collections module.

Part of the operational and development scripts.

Public symbols: main.
"""
import asyncio
import yaml
from pathlib import Path

from app.config import settings
from app.db.mongo import MongoClient          
from app.ingestion.collections import CollectionConfig, CollectionRegistry


async def main():
    """Compute the main."""
    mongo = MongoClient()
    registry = CollectionRegistry(mongo)
    base = Path(__file__).resolve().parent.parent / "workflows" / "collections"
    files = sorted(base.glob("*.yaml"))
    if not files:
        raise SystemExit(f"no collection configs found in {base}")   # fail loud, not silent
    for f in files:
        cfg = CollectionConfig(**yaml.safe_load(f.read_text()))
        await registry.upsert(cfg)
        print(f"registered: {cfg.collection_id} -> {cfg.doc_types}")
    await mongo.close()


asyncio.run(main())