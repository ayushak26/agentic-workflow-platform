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
    def __call__(
        self, result_sets: list[list[RetrievedChunk]], *, limit: int
    ) -> list[RetrievedChunk]: ...


class Reranker(Protocol):
    async def __call__(
        self, *, query: str, candidates: list[RetrievedChunk], top_n: int, llm: Any, model: str
    ) -> tuple[list[RetrievedChunk], float]: ...


class ContextExpander(Protocol):
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
