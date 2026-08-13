"""Parent/sentence-window/contextual expansion and final context assembly.

Also declares :class:`GraphContextProvider`, the explicit boundary a future
Graph RAG implementation would hook into: it would contribute scored context
after scoped vector retrieval and before context assembly, without changing
the Collection / Retrieval Profile / RAG Agent / Workflow contracts. No graph
database integration exists yet — this is a placeholder seam, not a feature.
"""
from __future__ import annotations

from typing import Any, Protocol

import tiktoken

from app.retrieval.models import RetrievalFilters, RetrievedChunk

_ENCODING = tiktoken.get_encoding("cl100k_base")


class GraphContextProvider(Protocol):
    async def expand(
        self, chunks: list[RetrievedChunk], *, query: str
    ) -> list[RetrievedChunk]: ...


async def expand_context(
    chunks: list[RetrievedChunk],
    *,
    strategy: str,
    client: Any,
    collection_name: str,
    filters: RetrievalFilters,
    index_id: str | None,
) -> list[RetrievedChunk]:
    """Apply the requested context-expansion strategy to already-kept chunks.

    ``sentence_window`` and ``contextual`` expansion is baked in at ingestion
    time (see ``app.ingestion.strategies``) — the enriched surface already
    lives on the chunk as ``context_content``/``retrieval_content``, so this
    is a pass-through for those strategies. ``parent`` expansion is the one
    strategy that needs a second datastore round-trip, since only the child
    chunk was retrieved.
    """

    if strategy != "parent" or not chunks:
        return chunks

    from app.retrieval.strategies import fetch_chunks_by_id  # avoid import cycle at module load

    parent_ids = sorted({c.parent_chunk_id for c in chunks if c.parent_chunk_id})
    if not parent_ids:
        return chunks
    parents = await fetch_chunks_by_id(
        client=client, collection_name=collection_name, chunk_ids=parent_ids,
        filters=filters, index_id=index_id,
    )
    parent_by_id = {parent.chunk_id: parent for parent in parents}
    for chunk in chunks:
        parent = parent_by_id.get(chunk.parent_chunk_id or "")
        if parent is not None:
            chunk.context_content = parent.text
            chunk.expanded_from_chunk_id = chunk.chunk_id
    return chunks


def assemble_context(chunks: list[RetrievedChunk]) -> tuple[str, int]:
    """Build the final ``[N] (source: ...)`` block passed to generation.

    Content preference per chunk: compressed (if the compressor ran) >
    context-expanded surface > what was actually embedded/searched > the raw
    chunk text. Numbering here is positional and local to this result — it is
    independent of the citation ``display_number`` stamped at ingestion.
    """

    parts: list[str] = []
    for position, chunk in enumerate(chunks, start=1):
        body = chunk.compressed_text or chunk.context_content or chunk.retrieval_content or chunk.text
        parts.append(f"[{position}] (source: {chunk.doc_title})\n{body}")
    context = "\n\n".join(parts)
    return context, len(_ENCODING.encode(context))
