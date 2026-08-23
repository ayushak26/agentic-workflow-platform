"""Small interfaces for retrieval, fusion, reranking and context expansion.

Documentation/extension seams — ``RetrievalService`` calls the concrete
functions in ``strategies.py``/``fusion.py``/``reranker.py``/``context.py``
directly. These Protocols exist so an alternative implementation (a
different vector store adapter, a different reranker) has a contract to
satisfy without inheriting from anything.
"""
from __future__ import annotations

from typing import Any, Protocol

from app.retrieval.models import RetrievalFilters, RetrievedChunk


class RetrievalStrategy(Protocol):
    """Provides the RetrievalStrategy behaviour."""
    async def __call__(
        self,
        *,
        client: Any,
        collection_name: str,
        query: str,
        filters: RetrievalFilters,
        index_id: str | None,
        top_k: int,
    ) -> tuple[list[RetrievedChunk], float]: ...


class FusionStrategy(Protocol):
    """Provides the FusionStrategy behaviour."""
    def __call__(
        self, result_sets: list[list[RetrievedChunk]], *, limit: int
    ) -> list[RetrievedChunk]: ...


class Reranker(Protocol):
    """Provides the Reranker behaviour."""
    async def __call__(
        self, *, query: str, candidates: list[RetrievedChunk], top_n: int, llm: Any, model: str
    ) -> tuple[list[RetrievedChunk], float]: ...


class ContextExpander(Protocol):
    """Provides the ContextExpander behaviour."""
    async def __call__(
        self,
        chunks: list[RetrievedChunk],
        *,
        strategy: str,
        client: Any,
        collection_name: str,
        filters: RetrievalFilters,
        index_id: str | None,
    ) -> list[RetrievedChunk]: ...
