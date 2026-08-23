"""Replaceable dense, sparse and hybrid Weaviate retrieval strategies."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from weaviate.classes.query import MetadataQuery

from app.ingestion.embedder import Embedder
from app.retrieval.filters import build_secure_where_filter
from app.retrieval.weaviate_client import VECTOR_NAME
from app.retrieval.models import RetrievalFilters, RetrievedChunk


def _chunk_from_object(obj: Any, *, score_kind: str, rank: int) -> RetrievedChunk:
    """Chunk the from object.

    Args:
        obj (Any): The obj.
        score_kind (str): The score kind.
        rank (int): The rank.

    Returns:
        RetrievedChunk: The from object.
    """
    properties = dict(obj.properties)
    metadata = getattr(obj, "metadata", None)
    raw_score = getattr(metadata, "score", None)
    distance = getattr(metadata, "distance", None)
    certainty = getattr(metadata, "certainty", None)
    if score_kind == "dense":
        score = certainty if certainty is not None else (1.0 - distance if distance is not None else raw_score)
    else:
        score = raw_score
    excluded = {"text", "retrieval_content", "context_content"}
    chunk = RetrievedChunk(
        chunk_id=str(properties.get("chunk_id") or getattr(obj, "uuid", "")),
        display_number=properties.get("display_number"),
        doc_id=str(properties.get("document_id") or properties.get("source_path") or ""),
        doc_title=str(properties.get("title") or str(properties.get("source_path", "")).split("/")[-1]),
        doc_type=str(properties.get("document_type") or properties.get("doc_type") or ""),
        text=str(properties.get("text") or ""),
        retrieval_content=properties.get("retrieval_content"),
        context_content=properties.get("context_content") or None,
        metadata={key: value for key, value in properties.items() if key not in excluded},
        dense_score=float(score) if score_kind == "dense" and score is not None else None,
        sparse_score=float(score) if score_kind == "sparse" and score is not None else None,
        fusion_score=float(score) if score_kind == "hybrid" and score is not None else None,
        hybrid_score=float(score or 0.0),
        parent_chunk_id=properties.get("parent_chunk_id") or None,
        document_id=properties.get("document_id") or None,
        source_id=properties.get("source_id") or None,
        source_version_id=properties.get("source_version_id") or None,
        index_id=properties.get("index_id") or None,
        page=int(properties.get("page") or 0) or None,
        section=properties.get("section") or None,
        rank=rank,
    )
    return chunk


async def dense_search(
    *,
    client: Any,
    collection_name: str,
    embedder: Embedder,
    query: str,
    filters: RetrievalFilters,
    index_id: str | None,
    top_k: int,
    exclude_parent_chunks: bool = False,
) -> tuple[list[RetrievedChunk], float]:
    """Compute the dense search.

    Args:
        client (Any): Client instance.
        collection_name (str): The collection name.
        embedder (Embedder): The embedder.
        query (str): Query filter.
        filters (RetrievalFilters): The filters.
        index_id (str | None): The index id.
        top_k (int): The top k.
        exclude_parent_chunks (bool): The exclude parent chunks (optional, default False).

    Returns:
        tuple[list[RetrievedChunk], float]: The search.
    """
    started = time.perf_counter()
    vector = (await embedder.embed([query]))[0]
    collection = client.collections.get(collection_name)
    response = await asyncio.to_thread(
        collection.query.near_vector,
        near_vector=vector,
        target_vector=VECTOR_NAME,
        limit=top_k,
        filters=build_secure_where_filter(
            filters, index_id=index_id, exclude_parent_chunks=exclude_parent_chunks
        ),
        return_metadata=MetadataQuery(distance=True, certainty=True),
    )
    chunks = [_chunk_from_object(obj, score_kind="dense", rank=i) for i, obj in enumerate(response.objects, 1)]
    return chunks, (time.perf_counter() - started) * 1000


async def sparse_search(
    *,
    client: Any,
    collection_name: str,
    query: str,
    filters: RetrievalFilters,
    index_id: str | None,
    top_k: int,
    exclude_parent_chunks: bool = False,
) -> tuple[list[RetrievedChunk], float]:
    """Compute the sparse search.

    Args:
        client (Any): Client instance.
        collection_name (str): The collection name.
        query (str): Query filter.
        filters (RetrievalFilters): The filters.
        index_id (str | None): The index id.
        top_k (int): The top k.
        exclude_parent_chunks (bool): The exclude parent chunks (optional, default False).

    Returns:
        tuple[list[RetrievedChunk], float]: The search.
    """
    started = time.perf_counter()
    collection = client.collections.get(collection_name)
    response = await asyncio.to_thread(
        collection.query.bm25,
        query=query,
        query_properties=["retrieval_content^2", "text", "title", "section"],
        limit=top_k,
        filters=build_secure_where_filter(
            filters, index_id=index_id, exclude_parent_chunks=exclude_parent_chunks
        ),
        return_metadata=MetadataQuery(score=True, explain_score=True),
    )
    chunks = [_chunk_from_object(obj, score_kind="sparse", rank=i) for i, obj in enumerate(response.objects, 1)]
    return chunks, (time.perf_counter() - started) * 1000


async def native_hybrid_search(
    *,
    client: Any,
    collection_name: str,
    embedder: Embedder,
    query: str,
    filters: RetrievalFilters,
    index_id: str | None,
    top_k: int,
    alpha: float,
    exclude_parent_chunks: bool = False,
) -> tuple[list[RetrievedChunk], float]:
    """Compute the native hybrid search.

    Args:
        client (Any): Client instance.
        collection_name (str): The collection name.
        embedder (Embedder): The embedder.
        query (str): Query filter.
        filters (RetrievalFilters): The filters.
        index_id (str | None): The index id.
        top_k (int): The top k.
        alpha (float): The alpha.
        exclude_parent_chunks (bool): The exclude parent chunks (optional, default False).

    Returns:
        tuple[list[RetrievedChunk], float]: The hybrid search.
    """
    from weaviate.classes.query import HybridFusion

    started = time.perf_counter()
    vector = (await embedder.embed([query]))[0]
    collection = client.collections.get(collection_name)
    response = await asyncio.to_thread(
        collection.query.hybrid,
        query=query,
        query_properties=["retrieval_content^2", "text", "title", "section"],
        vector=vector,
        target_vector=VECTOR_NAME,
        alpha=alpha,
        limit=top_k,
        filters=build_secure_where_filter(
            filters, index_id=index_id, exclude_parent_chunks=exclude_parent_chunks
        ),
        fusion_type=HybridFusion.RELATIVE_SCORE,
        return_metadata=MetadataQuery(score=True, explain_score=True),
    )
    chunks = [_chunk_from_object(obj, score_kind="hybrid", rank=i) for i, obj in enumerate(response.objects, 1)]
    return chunks, (time.perf_counter() - started) * 1000


async def fetch_chunks_by_id(
    *, client: Any, collection_name: str, chunk_ids: list[str], filters: RetrievalFilters,
    index_id: str | None = None,
) -> list[RetrievedChunk]:
    """Fetch the chunks by id.

    Args:
        client (Any): Client instance.
        collection_name (str): The collection name.
        chunk_ids (list[str]): Weaviate chunk identifiers.
        filters (RetrievalFilters): The filters.
        index_id (str | None): The index id (optional, default None).

    Returns:
        list[RetrievedChunk]: The chunks by id.
    """
    if not chunk_ids:
        return []
    from weaviate.classes.query import Filter

    where = build_secure_where_filter(filters, index_id=index_id) & Filter.any_of(
        [Filter.by_property("chunk_id").equal(value) for value in chunk_ids]
    )
    collection = client.collections.get(collection_name)
    response = await asyncio.to_thread(collection.query.fetch_objects, filters=where, limit=len(chunk_ids))
    return [_chunk_from_object(obj, score_kind="hybrid", rank=i) for i, obj in enumerate(response.objects, 1)]
