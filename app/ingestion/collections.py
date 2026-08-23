# app/ingestion/collections.py
"""Per-collection configuration: the controlled vocabulary that makes the
platform use-case-agnostic.

A CollectionConfig declares what a given use case's document corpus looks like
— its allowed doc_types, its default domain/industry — without the platform
code knowing anything about proposals, meetings, or any specific domain.
The use case is DATA (a config in Mongo), not a code path.

Validation lives here and is invoked at RetrievalFilters construction (cheap,
fail-fast, before any embedding or network spend) and at ingest (before insert).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class CollectionConfig(BaseModel):
    """Declares one use case's document taxonomy.

    Stored in Mongo `collections`. Loaded by CollectionRegistry. The platform
    ships zero hard-coded collections — proposal generation is just one config
    among many (see workflows/collections/proposal.yaml).
    """

    collection_id: str = Field(..., min_length=1)
    display_name: str
    doc_types: list[str] = Field(..., min_length=1)  # the controlled vocabulary
    default_industry: Optional[str] = None
    description: str = ""

    def validate_doc_type(self, dt: str) -> None:
        """Raise if `dt` is not in this collection's declared vocabulary.

        Called at ingest (per document) and at retrieval (per filter). One
        method, both boundaries — the vocabulary has exactly one source of truth.
        """
        if dt not in self.doc_types:
            raise ValueError(
                f"doc_type {dt!r} is not declared in collection "
                f"{self.collection_id!r}; allowed: {self.doc_types}"
            )

    def validate_doc_types(self, dts: list[str]) -> None:
        """Validate a list (retrieval filters carry a list of doc_types)."""
        for dt in dts:
            self.validate_doc_type(dt)


class CollectionRegistry:
    """Loads/saves CollectionConfig via the MongoClient's `collections`
    accessor — never touches the raw db, matching the project's convention
    that callers get named collections."""

    def __init__(self, mongo) -> None:        # mongo: MongoClient (the wrapper)
        """Initialize the CollectionRegistry.

        Args:
            mongo: The mongo.
        """
        self._col = mongo.collections          # the named accessor, not a raw db
        self._cache: dict[str, CollectionConfig] = {}

    async def get(self, collection_id: str) -> CollectionConfig:
        """Return the result.

        Args:
            collection_id (str): Knowledge collection identifier.

        Returns:
            CollectionConfig: The result.
        """
        if collection_id in self._cache:
            return self._cache[collection_id]
        doc = await self._col.find_one({"collection_id": collection_id})
        if doc is None:
            raise KeyError(
                f"collection {collection_id!r} is not registered; "
                f"seed it via scripts/seed_collections.py first"
            )
        doc.pop("_id", None)
        doc.pop("updated_at", None)
        cfg = CollectionConfig(**doc)
        self._cache[collection_id] = cfg
        return cfg

    async def upsert(self, cfg: CollectionConfig) -> None:
        """Upsert the result.

        Args:
            cfg (CollectionConfig): The cfg.
        """
        from datetime import datetime, timezone
        await self._col.update_one(
            {"collection_id": cfg.collection_id},
            {"$set": {**cfg.model_dump(), "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        self._cache[cfg.collection_id] = cfg

    async def list_all(self) -> list[CollectionConfig]:
        """List the all.

        Returns:
            list[CollectionConfig]: The all.
        """
        out: list[CollectionConfig] = []
        async for doc in self._col.find({}):
            doc.pop("_id", None)
            doc.pop("updated_at", None)
            out.append(CollectionConfig(**doc))
        return out