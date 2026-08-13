"""Protocols and the stage registry for the ingestion pipeline.

These wrap the existing extract → chunk → embed → store implementation
behind small interfaces so a parser/chunker/enricher/embedder/index strategy
can be swapped or added per-profile without ingestion.jobs knowing which
concrete implementation it is running.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from app.ingestion.chunker import Chunk
from app.ingestion.extractor import ExtractedDocument


class DocumentParser(Protocol):
    async def parse(self, path: Path, *, config: dict[str, Any]) -> ExtractedDocument: ...


class ChunkingStrategy(Protocol):
    async def chunk(
        self, document: ExtractedDocument, *, config: dict[str, Any], chunk_id_prefix: str
    ) -> list[Chunk]: ...


class ChunkEnricher(Protocol):
    async def enrich(
        self, chunks: list[Chunk], *, document: ExtractedDocument, config: dict[str, Any]
    ) -> list[Chunk]: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class SearchIndex(Protocol):
    async def index(self, objects: list[dict[str, Any]], vectors: list[list[float]]) -> int:
        """Write ``objects`` (with their parallel ``vectors``) and return the
        number of objects actually inserted, so the caller can detect a
        partial write."""
        ...


class UnknownStrategyError(LookupError):
    pass


class StageRegistry:
    """Name -> strategy lookup for every ingestion stage kind.

    A profile stores a strategy *name* (``"parent_child"``, ``"layout_aware"``,
    ...); the registry is what turns that name back into a live strategy
    instance at run time.
    """

    def __init__(self) -> None:
        self.parsers: dict[str, DocumentParser] = {}
        self.chunkers: dict[str, ChunkingStrategy] = {}
        self.enrichers: dict[str, ChunkEnricher] = {}

    def parser(self, name: str, instance: DocumentParser) -> None:
        self.parsers[name] = instance

    def chunker(self, name: str, instance: ChunkingStrategy) -> None:
        self.chunkers[name] = instance

    def enricher(self, name: str, instance: ChunkEnricher) -> None:
        self.enrichers[name] = instance

    def get_parser(self, name: str) -> DocumentParser:
        try:
            return self.parsers[name]
        except KeyError as exc:
            raise UnknownStrategyError(f"no registered parser strategy {name!r}") from exc

    def get_chunker(self, name: str) -> ChunkingStrategy:
        try:
            return self.chunkers[name]
        except KeyError as exc:
            raise UnknownStrategyError(f"no registered chunking strategy {name!r}") from exc
