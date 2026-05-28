"""Hybrid RAG retrieval pipeline.

Public API:
    retrieve(query: RetrievalQuery, *, weaviate_client, llm) -> RetrievalResult
"""
from app.retrieval.models import (
    DocType,
    RetrievalFilters,
    RetrievalQuery,
    RetrievalResult,
    RetrievedChunk,
)
from app.retrieval.retriever import retrieve

__all__ = [
    "retrieve",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievalFilters",
    "RetrievedChunk",
    "DocType",
]