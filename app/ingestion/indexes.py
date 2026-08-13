"""Narrow Weaviate index writer used by ingestion.

Logical index identity (which Collection, which profile versions) never
reaches this module — only the physical collection name an ``IndexVersion``
resolved to. It takes the same raw, already-connected ``weaviate.WeaviateClient``
that ``services["weaviate_client"]`` holds and that ``app.retrieval.strategies``
reads from — there is exactly one live Weaviate connection in the process.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.retrieval.weaviate_client import ensure_collection_schema_on, upsert_objects_on


class WeaviateSearchIndex:
    """Satisfies :class:`app.ingestion.contracts.SearchIndex` for one physical
    Weaviate collection.

    One instance is scoped to a single ``IndexVersion``'s physical
    collection; the coordinator constructs a fresh one per ingestion job.
    """

    def __init__(self, *, client: Any, collection_name: str):
        self._client = client
        self._collection_name = collection_name
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        await asyncio.to_thread(ensure_collection_schema_on, self._client, self._collection_name)
        self._schema_ready = True

    async def index(self, objects: list[dict[str, Any]], vectors: list[list[float]]) -> int:
        await self._ensure_schema()
        return await asyncio.to_thread(
            upsert_objects_on, self._client, self._collection_name, objects, vectors
        )
