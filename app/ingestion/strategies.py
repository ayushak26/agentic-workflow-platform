"""Built-in parser, chunking and enrichment strategies."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Callable

import tiktoken

from app.ingestion.chunker import Chunk, ChunkConfig, chunk_document
from app.ingestion.contracts import StageRegistry
from app.ingestion.extractor import ExtractedDocument, ExtractedUnit, get_extractor


class StandardDocumentParser:
    """Provides the StandardDocumentParser behaviour."""
    async def parse(self, path: Path, *, config: dict[str, Any]) -> ExtractedDocument:
        """Parse the result.

        Args:
            path (Path): Filesystem path.
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            ExtractedDocument: The result.
        """
        return await asyncio.to_thread(get_extractor(path).extract, path)


class LayoutAwareDocumentParser(StandardDocumentParser):
    """Use reliable native page/slide/sheet/heading structure when available.

    Current extractors already preserve PDF pages, slides, sheets and HTML/JSON
    sections.  This adapter makes that behavior an explicit profile boundary.
    """


class StructureAwareDocumentParser(LayoutAwareDocumentParser):
    """Provides the StructureAwareDocumentParser behaviour."""
    async def parse(self, path: Path, *, config: dict[str, Any]) -> ExtractedDocument:
        """Parse the result.

        Args:
            path (Path): Filesystem path.
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            ExtractedDocument: The result.
        """
        document = await super().parse(path, config=config)
        for unit in document.units:
            unit.text = unit.text.strip()
        document.metadata["structure_aware"] = "true"
        return document


class OcrFallbackDocumentParser(StandardDocumentParser):
    """Fall back to reading the page as an image when there is no text layer.

    With no explicit OCR callable configured, a vision model transcribes the
    rendered pages — the same provider used by ``vision_augmented``.
    """

    def __init__(
        self,
        ocr: Callable[[Path], ExtractedDocument] | None = None,
        describer: Any | None = None,
    ):
        """Initialize the OcrFallbackDocumentParser.

        Args:
            ocr (Callable[[Path], ExtractedDocument] | None): The ocr (optional, default None).
            describer (Any | None): The describer (optional, default None).
        """
        self._ocr = ocr
        self._describer = describer

    async def parse(self, path: Path, *, config: dict[str, Any]) -> ExtractedDocument:
        """Parse the result.

        Args:
            path (Path): Filesystem path.
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            ExtractedDocument: The result.
        """
        document = await super().parse(path, config=config)
        minimum = int(config.get("ocr_min_text_characters", 80))
        if len(document.full_text.strip()) >= minimum:
            return document
        if self._ocr is not None:
            return await asyncio.to_thread(self._ocr, path)

        describer = self._describer
        if describer is None:
            from app.ingestion.vision import PdfPageVisionDescriber

            describer = PdfPageVisionDescriber()
        if path.suffix.lower() != ".pdf" or not describer.available():
            reason = (
                "the source is not a PDF"
                if path.suffix.lower() != ".pdf"
                else describer.unavailable_reason()
            )
            raise ValueError(
                "OCR fallback was requested because native extraction returned "
                f"insufficient text, but no OCR provider is available: {reason}"
            )
        targets = [unit.index for unit in document.units] or [0]
        described = await describer.describe_pages(
            path, targets, prompt=OCR_PROMPT
        )
        if not described:
            raise ValueError(
                "OCR fallback ran but the vision model returned no text for "
                f"{path.name}"
            )
        by_index = {unit.index: unit for unit in document.units}
        for index, description in described.items():
            if index in by_index:
                by_index[index].text = description.text
            else:
                document.units.append(
                    ExtractedUnit(index=index, label=f"page {index + 1}", text=description.text)
                )
        document.units.sort(key=lambda unit: unit.index)
        document.metadata["ocr_model"] = describer.model
        return document


OCR_PROMPT = (
    "Transcribe every word visible on this page, in reading order. Reproduce "
    "tables as markdown tables. Do not summarise or add commentary. If the "
    "page is genuinely blank, reply with exactly NO_VISUAL_CONTENT."
)


class VisionAugmentedDocumentParser(StandardDocumentParser):
    """Add vision-model transcriptions of figures, charts and image tables.

    Native extraction keeps the page's real text; the vision pass adds what
    only exists visually. Both land in the same unit, so a retrieved chunk can
    quote a pump curve or a compatibility matrix that no text layer contained.

    Non-PDF sources fall through to the standard parser untouched, and a vision
    failure degrades to text-only rather than failing the document.
    """

    def __init__(self, describer: Any | None = None):
        """Initialize the VisionAugmentedDocumentParser.

        Args:
            describer (Any | None): The describer (optional, default None).
        """
        self._describer = describer

    async def parse(self, path: Path, *, config: dict[str, Any]) -> ExtractedDocument:
        """Parse the result.

        Args:
            path (Path): Filesystem path.
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            ExtractedDocument: The result.
        """
        document = await super().parse(path, config=config)
        if path.suffix.lower() != ".pdf":
            return document

        describer = self._describer
        if describer is None:
            from app.ingestion.vision import PdfPageVisionDescriber

            describer = PdfPageVisionDescriber()
        if not describer.available():
            raise ValueError(
                "vision-augmented parsing was requested but is unavailable: "
                f"{describer.unavailable_reason()}"
            )

        from app.ingestion.vision import pages_with_visual_content

        if bool(config.get("vision_all_pages", False)):
            targets = [unit.index for unit in document.units]
        else:
            targets = await asyncio.to_thread(pages_with_visual_content, path)
        budget = int(config.get("vision_max_pages", 20))
        targets = targets[:budget] if budget >= 0 else targets
        if not targets:
            return document

        described = await describer.describe_pages(
            path, targets, prompt=(config.get("vision_prompt") or None)
        )
        by_index = {unit.index: unit for unit in document.units}
        for index, description in described.items():
            unit = by_index.get(index)
            if unit is None:
                continue
            unit.text = (
                f"{unit.text}\n\n[Visual content]\n{description.text}".strip()
                if unit.text.strip()
                else description.text
            )
        document.metadata["vision_pages_described"] = str(len(described))
        document.metadata["vision_model"] = describer.model
        return document


def _chunk_config(config: dict[str, Any]) -> ChunkConfig:
    """Chunk the config.

    Args:
        config (dict[str, Any]): Node configuration mapping.

    Returns:
        ChunkConfig: The config.
    """
    fields = ChunkConfig.__dataclass_fields__
    return ChunkConfig(**{key: value for key, value in config.items() if key in fields})


class RecursiveChunkingStrategy:
    """Provides the RecursiveChunkingStrategy behaviour."""
    async def chunk(
        self,
        document: ExtractedDocument,
        *,
        config: dict[str, Any],
        chunk_id_prefix: str,
    ) -> list[Chunk]:
        """Chunk the result.

        Args:
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.
            chunk_id_prefix (str): The chunk id prefix.

        Returns:
            list[Chunk]: The result.
        """
        return chunk_document(document, _chunk_config(config), chunk_id_prefix=chunk_id_prefix)


class FixedTokenChunkingStrategy:
    """Provides the FixedTokenChunkingStrategy behaviour."""
    async def chunk(
        self,
        document: ExtractedDocument,
        *,
        config: dict[str, Any],
        chunk_id_prefix: str,
    ) -> list[Chunk]:
        """Chunk the result.

        Args:
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.
            chunk_id_prefix (str): The chunk id prefix.

        Returns:
            list[Chunk]: The result.
        """
        cfg = _chunk_config(config)
        encoding = tiktoken.get_encoding(cfg.tokenizer_name)
        output: list[Chunk] = []
        step = max(1, cfg.target_tokens - cfg.overlap_tokens)
        for unit in document.units:
            tokens = encoding.encode(unit.text)
            for start in range(0, len(tokens), step):
                part = tokens[start : start + cfg.target_tokens]
                if len(part) < cfg.min_tokens:
                    continue
                index = len(output)
                output.append(
                    Chunk(
                        chunk_id=f"{chunk_id_prefix}::unit{unit.index}::chunk{index}",
                        text=encoding.decode(part),
                        token_count=len(part),
                        metadata={
                            "source_path": document.source_path,
                            "source_format": document.source_format,
                            "unit_index": unit.index,
                            "unit_label": unit.label,
                            "chunk_index": index,
                        },
                    )
                )
        return output


class StructureAwareChunkingStrategy(RecursiveChunkingStrategy):
    """Provides the StructureAwareChunkingStrategy behaviour."""
    async def chunk(self, document: ExtractedDocument, *, config: dict[str, Any], chunk_id_prefix: str) -> list[Chunk]:
        """Chunk the result.

        Args:
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.
            chunk_id_prefix (str): The chunk id prefix.

        Returns:
            list[Chunk]: The result.
        """
        chunks = await super().chunk(document, config=config, chunk_id_prefix=chunk_id_prefix)
        title = document.metadata.get("title")
        for chunk in chunks:
            chunk.title = title
            chunk.section = str(chunk.metadata.get("unit_label") or "")
        return chunks


class ParentChildChunkingStrategy(RecursiveChunkingStrategy):
    """Provides the ParentChildChunkingStrategy behaviour."""
    async def chunk(self, document: ExtractedDocument, *, config: dict[str, Any], chunk_id_prefix: str) -> list[Chunk]:
        """Chunk the result.

        Args:
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.
            chunk_id_prefix (str): The chunk id prefix.

        Returns:
            list[Chunk]: The result.
        """
        child_config = dict(config)
        children = await super().chunk(document, config=child_config, chunk_id_prefix=chunk_id_prefix)
        by_unit: dict[int, list[Chunk]] = {}
        for child in children:
            by_unit.setdefault(int(child.metadata.get("unit_index", 0)), []).append(child)
        output: list[Chunk] = []
        encoding = tiktoken.get_encoding(str(config.get("tokenizer_name", "cl100k_base")))
        parent_tokens = int(config.get("parent_tokens", 1536))
        for unit_index, unit_children in by_unit.items():
            unit = next(item for item in document.units if item.index == unit_index)
            parent_id = f"{chunk_id_prefix}::unit{unit_index}::parent"
            parent_vector = encoding.encode(unit.text)[:parent_tokens]
            parent_text = encoding.decode(parent_vector)
            parent = Chunk(
                chunk_id=parent_id,
                text=parent_text,
                retrieval_content=parent_text,
                token_count=len(parent_vector),
                metadata={
                    "source_path": document.source_path,
                    "source_format": document.source_format,
                    "unit_index": unit_index,
                    "unit_label": unit.label,
                    "chunk_index": -1,
                },
                section=unit.label,
                chunk_role="parent",
            )
            output.append(parent)
            for child in unit_children:
                child.parent_chunk_id = parent_id
                child.section = unit.label
                output.append(child)
        return output


class ContextualChunkingStrategy(StructureAwareChunkingStrategy):
    """Provides the ContextualChunkingStrategy behaviour."""
    async def chunk(self, document: ExtractedDocument, *, config: dict[str, Any], chunk_id_prefix: str) -> list[Chunk]:
        """Chunk the result.

        Args:
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.
            chunk_id_prefix (str): The chunk id prefix.

        Returns:
            list[Chunk]: The result.
        """
        chunks = await super().chunk(document, config=config, chunk_id_prefix=chunk_id_prefix)
        title = str(document.metadata.get("title") or Path(document.source_path).name)
        for chunk in chunks:
            section = chunk.section or str(chunk.metadata.get("unit_label") or "")
            chunk.retrieval_content = f"Document: {title}\nSection: {section}\n\n{chunk.text}"
        return chunks


_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class SentenceWindowChunkingStrategy:
    """Provides the SentenceWindowChunkingStrategy behaviour."""
    async def chunk(self, document: ExtractedDocument, *, config: dict[str, Any], chunk_id_prefix: str) -> list[Chunk]:
        """Chunk the result.

        Args:
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.
            chunk_id_prefix (str): The chunk id prefix.

        Returns:
            list[Chunk]: The result.
        """
        window = int(config.get("sentence_window", 2))
        encoding = tiktoken.get_encoding(str(config.get("tokenizer_name", "cl100k_base")))
        output: list[Chunk] = []
        for unit in document.units:
            sentences = [value.strip() for value in _SENTENCE.split(unit.text) if value.strip()]
            for index, sentence in enumerate(sentences):
                start, end = max(0, index - window), min(len(sentences), index + window + 1)
                output.append(
                    Chunk(
                        chunk_id=f"{chunk_id_prefix}::unit{unit.index}::sentence{index}",
                        text=sentence,
                        retrieval_content=sentence,
                        context_content=" ".join(sentences[start:end]),
                        token_count=len(encoding.encode(sentence)),
                        metadata={
                            "source_path": document.source_path,
                            "source_format": document.source_format,
                            "unit_index": unit.index,
                            "unit_label": unit.label,
                            "chunk_index": len(output),
                            "sentence_index": index,
                        },
                        section=unit.label,
                    )
                )
        return output


def _terms(text: str) -> set[str]:
    """Internal helper for the terms step.

    Args:
        text (str): The text.

    Returns:
        set[str]: The result.
    """
    return {term for term in re.findall(r"[a-z0-9]{3,}", text.lower())}


class SemanticChunkingStrategy:
    """Lightweight semantic-boundary strategy using paragraph topic overlap.

    It intentionally avoids a second embedding pass.  Paragraphs with little
    lexical continuity start a new segment, after which the normal recursive
    chunker enforces token ceilings.
    """

    async def chunk(self, document: ExtractedDocument, *, config: dict[str, Any], chunk_id_prefix: str) -> list[Chunk]:
        """Chunk the result.

        Args:
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.
            chunk_id_prefix (str): The chunk id prefix.

        Returns:
            list[Chunk]: The result.
        """
        threshold = float(config.get("semantic_similarity_threshold", 0.12))
        units: list[ExtractedUnit] = []
        for original in document.units:
            paragraphs = [item.strip() for item in re.split(r"\n\s*\n", original.text) if item.strip()]
            groups: list[list[str]] = []
            for paragraph in paragraphs:
                if not groups:
                    groups.append([paragraph])
                    continue
                left, right = _terms(groups[-1][-1]), _terms(paragraph)
                overlap = len(left & right) / max(1, len(left | right))
                if overlap < threshold:
                    groups.append([paragraph])
                else:
                    groups[-1].append(paragraph)
            units.extend(
                ExtractedUnit(index=len(units), label=f"{original.label} segment {i + 1}", text="\n\n".join(group))
                for i, group in enumerate(groups)
            )
        derived = ExtractedDocument(
            source_path=document.source_path,
            source_format=document.source_format,
            page_count=len(units),
            units=units,
            metadata=document.metadata,
        )
        return await RecursiveChunkingStrategy().chunk(derived, config=config, chunk_id_prefix=chunk_id_prefix)


class MetadataContextEnricher:
    """Provides the MetadataContextEnricher behaviour."""
    async def enrich(self, chunks: list[Chunk], *, document: ExtractedDocument, config: dict[str, Any]) -> list[Chunk]:
        """Compute the enrich.

        Args:
            chunks (list[Chunk]): The chunks.
            document (ExtractedDocument): Document.
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            list[Chunk]: The result.
        """
        title = str(document.metadata.get("title") or Path(document.source_path).name)
        for chunk in chunks:
            if config.get("prepend_context", False) and not chunk.retrieval_content:
                section = chunk.section or str(chunk.metadata.get("unit_label") or "")
                chunk.retrieval_content = f"Document: {title}\nSection: {section}\n\n{chunk.text}"
            chunk.title = chunk.title or title
        return chunks


def build_stage_registry() -> StageRegistry:
    """Build the stage registry.

    Returns:
        StageRegistry: The stage registry.
    """
    registry = StageRegistry()
    registry.parser("standard", StandardDocumentParser())
    registry.parser("layout_aware", LayoutAwareDocumentParser())
    registry.parser("structure_aware", StructureAwareDocumentParser())
    registry.parser("ocr_fallback", OcrFallbackDocumentParser())
    registry.parser("vision_augmented", VisionAugmentedDocumentParser())
    registry.chunker("fixed_token", FixedTokenChunkingStrategy())
    registry.chunker("recursive", RecursiveChunkingStrategy())
    registry.chunker("structure_aware", StructureAwareChunkingStrategy())
    registry.chunker("parent_child", ParentChildChunkingStrategy())
    registry.chunker("contextual", ContextualChunkingStrategy())
    registry.chunker("sentence_window", SentenceWindowChunkingStrategy())
    registry.chunker("semantic", SemanticChunkingStrategy())
    registry.enricher("metadata_context", MetadataContextEnricher())
    return registry


DEFAULT_STAGE_REGISTRY = build_stage_registry()
