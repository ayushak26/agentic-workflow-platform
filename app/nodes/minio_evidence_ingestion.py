"""MinIOEvidenceIngestion: index already-acquired full-text sources into
Weaviate so the drafters can be GROUNDED in the papers, not merely cite them.

Pipeline position: runs after CitationRegistryBuilder (which assigns the global
``display_number`` per source) and before the per-section RAGAgent retrievals.

What it does, deterministically per source in the numbered registry:
  1. reads the source's extracted page text from MinIO (``pages_object_key``,
     the pages.json the acquirer already stored - we do NOT re-parse the PDF);
  2. chunks the page text into retrieval-sized windows;
  3. embeds each chunk with the same embedder used everywhere else
     (text-embedding-3-small), so query and chunk vectors share one space;
  4. writes the chunks to the DocumentChunk collection via the
     ``evidence_indexer`` write service, stamping each chunk with:
       - ``display_number``  -> the global citation [N] (so a retrieved passage
                                carries its footnote number straight to the
                                drafter; this is what keeps footnotes aligned);
       - ``source_path``     -> the document identity (flows to doc_title in
                                retrieval);
       - ``session_id`` / ``collection_id`` from STATE - the same isolation
                                boundary RAGAgent later filters on, so ingestion
                                and retrieval always agree by construction.

The node is deterministic apart from the embedding call. It never drafts, never
verifies entailment - it only makes the acquired text retrievable and correctly
numbered.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)


class MinIOEvidenceIngestionInput(BaseModel):
    pass


class MinIOEvidenceIngestionConfig(BaseModel):
    # The numbered registry from CitationRegistryBuilder. Each entry must carry
    # display_number + citation_id; we also need the MinIO pages key, which the
    # registry entry does not have - so we ALSO take the raw documents list to
    # resolve pages_object_key per source. Both are templated in.
    citation_registry: str | list[dict[str, Any]] = Field(default_factory=list)
    documents: str | list[dict[str, Any]] = Field(default_factory=list)
    chunk_chars: int = Field(default=1200, ge=200, le=4000)
    chunk_overlap_chars: int = Field(default=150, ge=0, le=1000)
    max_chunks_per_source: int = Field(default=80, ge=1, le=400)
    max_total_chunks: int = Field(default=4000, ge=1, le=20000)
    embed_batch_size: int = Field(default=64, ge=1, le=256)

    @field_validator("citation_registry", "documents", mode="before")
    @classmethod
    def _reject_unresolved(cls, value: Any) -> Any:
        if isinstance(value, dict) and "documents" in value:
            return value["documents"]
        return value


class MinIOEvidenceIngestionOutput(BaseModel):
    sources_indexed: int = 0
    sources_skipped: int = 0
    chunks_written: int = 0
    collection_id: str = ""
    session_id: str = ""
    skipped_detail: list[dict[str, str]] = Field(default_factory=list)


@NodeRegistry.register
class MinIOEvidenceIngestion(NodeType):
    type_name = "MinIOEvidenceIngestion"
    description = (
        "Index acquired full-text sources (from MinIO pages.json) into "
        "Weaviate, stamping each chunk with its global citation display_number "
        "so retrieved passages carry their footnote number to the drafter."
    )
    input_schema: ClassVar[type[BaseModel]] = MinIOEvidenceIngestionInput
    config_schema: ClassVar[type[BaseModel]] = MinIOEvidenceIngestionConfig
    output_schema: ClassVar[type[BaseModel]] = MinIOEvidenceIngestionOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = MinIOEvidenceIngestionConfig(**resolved_config)
        if isinstance(cfg.citation_registry, str) or isinstance(
            cfg.documents, str
        ):
            raise ValueError(
                "citation_registry / documents templates did not resolve to "
                "lists"
            )

        store = self.services.get("object_store")
        embedder = self.services.get("embedder")
        indexer = self.services.get("evidence_indexer")
        missing = [
            name
            for name, svc in (
                ("object_store", store),
                ("embedder", embedder),
                ("evidence_indexer", indexer),
            )
            if svc is None
        ]
        if missing:
            raise RuntimeError(
                f"MinIOEvidenceIngestion requires services {missing}"
            )

        session_id = str(state["session_id"])
        collection_id = str(state["collection_id"])

        # Map document identity -> display_number from the registry. The
        # registry keys on citation_id (== version_id/document_id) and carries
        # the global [N]. We match documents to it by version_id/document_id.
        number_by_id: dict[str, int] = {}
        for entry in cfg.citation_registry:
            cid = str(entry.get("citation_id") or "")
            if cid and entry.get("display_number") is not None:
                number_by_id[cid] = int(entry["display_number"])

        # De-duplicate documents to one physical source per display_number, so
        # the same paper (repeated across claims) is ingested once.
        seen_numbers: set[int] = set()
        selected: list[tuple[int, dict[str, Any]]] = []
        skipped: list[dict[str, str]] = []
        for doc in cfg.documents:
            cid = str(doc.get("version_id") or doc.get("document_id") or "")
            number = number_by_id.get(cid)
            if number is None:
                # Not in the numbered registry (e.g. excluded non-full-text).
                skipped.append(
                    {"document_id": str(doc.get("document_id")),
                     "reason": "no_display_number"}
                )
                continue
            if number in seen_numbers:
                continue
            if not doc.get("pages_object_key"):
                skipped.append(
                    {"document_id": str(doc.get("document_id")),
                     "reason": "no_pages_object_key"}
                )
                continue
            seen_numbers.add(number)
            selected.append((number, doc))

        all_chunks: list[dict[str, Any]] = []
        for number, doc in selected:
            if len(all_chunks) >= cfg.max_total_chunks:
                break
            pages_key = str(doc["pages_object_key"])
            try:
                raw = await asyncio.to_thread(store.get_bytes, pages_key)
            except Exception as exc:
                skipped.append(
                    {"document_id": str(doc.get("document_id")),
                     "reason": f"pages_read_failed: {type(exc).__name__}"}
                )
                continue

            pages = _load_pages(raw)
            source_path = _source_path(doc, number)
            source_chunks = _chunk_pages(
                pages,
                chunk_chars=cfg.chunk_chars,
                overlap=cfg.chunk_overlap_chars,
                max_chunks=cfg.max_chunks_per_source,
            )
            for chunk_index, (unit_index, text) in enumerate(source_chunks):
                if len(all_chunks) >= cfg.max_total_chunks:
                    break
                all_chunks.append(
                    {
                        "chunk_id": f"ev-{number:04d}-{chunk_index:04d}",
                        "text": text,
                        "token_count": max(1, len(text) // 4),
                        "source_path": source_path,
                        "source_format": "evidence_pages_json",
                        "unit_index": unit_index,
                        "unit_label": f"page-{unit_index}",
                        "chunk_index": chunk_index,
                        "industry": "",
                        "doc_type": str(doc.get("source_type") or "evidence"),
                        "language": "en",
                        "session_id": session_id,
                        "collection_id": collection_id,
                        "display_number": number,
                        "ingested_at": _rfc3339_now(),
                    }
                )

        if not all_chunks:
            log.warning(
                "evidence_ingestion.no_chunks",
                node_id=self.node_id,
                sources=len(selected),
            )
            return MinIOEvidenceIngestionOutput(
                sources_indexed=0,
                sources_skipped=len(skipped),
                chunks_written=0,
                collection_id=collection_id,
                session_id=session_id,
                skipped_detail=skipped,
            ).model_dump(mode="json")

        # Embed in batches, then write. Vectors align 1:1 with chunks.
        vectors: list[list[float]] = []
        texts = [c["text"] for c in all_chunks]
        for start in range(0, len(texts), cfg.embed_batch_size):
            batch = texts[start : start + cfg.embed_batch_size]
            batch_vectors = await embedder.embed(batch)
            vectors.extend(batch_vectors)

        written = await asyncio.to_thread(indexer, all_chunks, vectors)

        log.info(
            "evidence_ingestion.done",
            node_id=self.node_id,
            sources=len(selected),
            chunks=written,
            collection_id=collection_id,
        )
        return MinIOEvidenceIngestionOutput(
            sources_indexed=len(selected),
            sources_skipped=len(skipped),
            chunks_written=int(written),
            collection_id=collection_id,
            session_id=session_id,
            skipped_detail=skipped,
        ).model_dump(mode="json")


def _load_pages(raw: bytes) -> list[dict[str, Any]]:
    payload = json.loads(raw.decode("utf-8"))
    pages = payload.get("pages") if isinstance(payload, dict) else payload
    return pages if isinstance(pages, list) else []


def _source_path(doc: dict[str, Any], number: int) -> str:
    # doc_title in retrieval is source_path.split("/")[-1], so end the path
    # with a human-meaningful, [N]-prefixed identity.
    title = str(doc.get("title") or "source").strip().replace("/", "-")[:80]
    return f"evidence/{number:04d}/{title}"


def _chunk_pages(
    pages: list[dict[str, Any]],
    *,
    chunk_chars: int,
    overlap: int,
    max_chunks: int,
) -> list[tuple[int, str]]:
    chunks: list[tuple[int, str]] = []
    for page in pages:
        page_no = int(page.get("page") or 0)
        text = str(page.get("text") or "").strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            window = text[start : start + chunk_chars].strip()
            if window:
                chunks.append((page_no, window))
                if len(chunks) >= max_chunks:
                    return chunks
            if overlap >= chunk_chars:
                start += chunk_chars
            else:
                start += chunk_chars - overlap
    return chunks


def _rfc3339_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
