"""Structure-aware recursive chunker.

Takes an ExtractedDocument and returns a list of Chunks ready for embedding.

Strategy (Huyen, AI Engineering, Ch. 6 RAG and Agents, page 268):
- Recursive splitting: try sections, fall back to paragraphs, fall back to
  sentences, fall back to hard token cuts. The earliest level that fits
  the budget wins.
- Overlap between adjacent chunks preserves cross-boundary context.
- Token-aware: budgets are in tokens (cl100k_base, the text-embedding-3-small
  tokenizer), not characters, so we never silently exceed the embedding
  model's context limit.

The chunker is pure: ExtractedDocument in, list[Chunk] out. No I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import tiktoken

from app.ingestion.extractor import ExtractedDocument, ExtractedUnit
from app.observability.logging import get_logger

log = get_logger(__name__)


# ---------- Data shape returned to the embedder -------------------------------


@dataclass
class Chunk:
    """One unit of text ready for embedding.

    `chunk_id` is the deterministic identifier — same content from the same
    source produces the same id, which makes the pipeline idempotent.
    `metadata` carries document-level fields (industry, doc_type, etc.) plus
    chunk-level fields (unit_index, chunk_index).
    """

    chunk_id: str
    text: str
    token_count: int
    metadata: dict[str, str | int] = field(default_factory=dict)

    # Knowledge Studio ingestion-strategy surface. All optional so the
    # original 4-field Chunk keeps working unchanged for every caller that
    # never sets them.
    retrieval_content: str | None = None   # what actually gets embedded/searched
    context_content: str | None = None     # sentence-window / parent surround
    title: str | None = None
    section: str | None = None
    parent_chunk_id: str | None = None
    chunk_role: str = "child"              # "child" | "parent"

    @property
    def embedding_content(self) -> str:
        """The text that should actually be embedded for this chunk.

        Falls back to the raw ``text`` when no strategy set a distinct
        ``retrieval_content`` (contextual enrichment, parent summarization).
        """

        return self.retrieval_content or self.text


# ---------- Config ------------------------------------------------------------


@dataclass
class ChunkConfig:
    """Tunable chunking parameters.

    Defaults shipped here are sensible for consulting/proposal-style prose.
    Phase 3+ allows per-workflow overrides via YAML.
    """

    target_tokens: int = 512        # preferred chunk size
    max_tokens: int = 1024          # hard ceiling
    overlap_tokens: int = 64        # ~12% overlap, defended in ADR 0002
    min_tokens: int = 50            # discard chunks smaller than this
    tokenizer_name: str = "cl100k_base"  # matches text-embedding-3-small
    parent_tokens: int = 1536       # parent_child strategy: parent record ceiling
    sentence_window: int = 2        # sentence_window strategy: sentences either side


# ---------- Internals: tokenizer + sentence splitter --------------------------


# Tokenizer is cached at module level — initialization is non-trivial.
_TOKENIZERS: dict[str, tiktoken.Encoding] = {}


def _get_tokenizer(name: str) -> tiktoken.Encoding:
    if name not in _TOKENIZERS:
        _TOKENIZERS[name] = tiktoken.get_encoding(name)
    return _TOKENIZERS[name]


# Section header pattern: matches lines like "## Heading", "1. Title", "1.2 Subtitle"
# Conservative — captures only obviously-heading-like lines, not in-prose mentions.
_SECTION_HEADER = re.compile(
    r"^\s*(?:#{1,6}\s+\S|\d+(?:\.\d+)*\.?\s+\S)", re.MULTILINE
)

# Sentence splitter: end-of-sentence punctuation followed by whitespace + capital.
# Not perfect (no NLP library), but cheap and good enough for our use case.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _split_sections(text: str) -> list[str]:
    """Split on top-level section headers. Returns list of section bodies."""
    matches = list(_SECTION_HEADER.finditer(text))
    if not matches:
        return [text]
    sections: list[str] = []
    starts = [m.start() for m in matches] + [len(text)]
    for i in range(len(matches)):
        sections.append(text[starts[i]:starts[i + 1]].strip())
    # Preserve any preamble before the first header
    preamble = text[: starts[0]].strip()
    if preamble:
        sections.insert(0, preamble)
    return [s for s in sections if s]


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines. Returns non-empty paragraph strings."""
    paras = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paras if p.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries. Best-effort, no NLP library."""
    sents = _SENTENCE_END.split(text)
    return [s.strip() for s in sents if s.strip()]


# ---------- Core chunking algorithm -------------------------------------------


def _hard_split(
    tokens: list[int], target: int, max_size: int, encoding: tiktoken.Encoding
) -> list[str]:
    """Last-resort: split a token list at fixed boundaries.

    Used when a single sentence exceeds max_tokens — rare but real (think
    table rows serialized as one long line).
    """
    chunks: list[str] = []
    i = 0
    while i < len(tokens):
        end = min(i + target, len(tokens))
        chunks.append(encoding.decode(tokens[i:end]))
        i = end
    return chunks


def _pack_pieces(
    pieces: list[str],
    encoding: tiktoken.Encoding,
    config: ChunkConfig,
) -> list[str]:
    """Greedy packer.

    Given a list of pieces (sections, paragraphs, or sentences), pack them
    into chunks that hit target_tokens without exceeding max_tokens. Pieces
    that are themselves too large get recursively split before packing.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0

    for piece in pieces:
        piece_tokens = len(encoding.encode(piece))

        # Piece too big on its own: recurse one level deeper.
        if piece_tokens > config.max_tokens:
            # Flush buffer first
            if buffer:
                chunks.append("\n\n".join(buffer))
                buffer = []
                buffer_tokens = 0
            # Recurse: try paragraphs, then sentences, then hard split
            chunks.extend(_split_oversized(piece, encoding, config))
            continue

        # Piece would push us over the hard ceiling: flush and start fresh
        if buffer_tokens + piece_tokens > config.max_tokens and buffer:
            chunks.append("\n\n".join(buffer))
            buffer = [piece]
            buffer_tokens = piece_tokens
            continue

        # Piece pushes us above target: include it, then flush
        buffer.append(piece)
        buffer_tokens += piece_tokens
        if buffer_tokens >= config.target_tokens:
            chunks.append("\n\n".join(buffer))
            buffer = []
            buffer_tokens = 0

    # Final flush
    if buffer:
        chunks.append("\n\n".join(buffer))

    return chunks


def _split_oversized(
    text: str, encoding: tiktoken.Encoding, config: ChunkConfig
) -> list[str]:
    """Recursive descent: paragraphs → sentences → hard cut."""
    paragraphs = _split_paragraphs(text)
    if len(paragraphs) > 1:
        return _pack_pieces(paragraphs, encoding, config)

    sentences = _split_sentences(text)
    if len(sentences) > 1:
        return _pack_pieces(sentences, encoding, config)

    # Single sentence (or sentence-less blob) too large for max_tokens
    tokens = encoding.encode(text)
    return _hard_split(tokens, config.target_tokens, config.max_tokens, encoding)


def _apply_overlap(
    chunks: list[str], encoding: tiktoken.Encoding, overlap_tokens: int
) -> list[str]:
    """Prepend the last `overlap_tokens` of chunk N-1 onto the start of chunk N.

    Huyen's example uses 20 chars on 2,048-char chunks (~1%). We use ~12%
    because consulting docs cross-reference heavily — see ADR 0002.
    """
    if overlap_tokens <= 0 or len(chunks) < 2:
        return chunks
    result = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tokens = encoding.encode(chunks[i - 1])
        if len(prev_tokens) <= overlap_tokens:
            tail = chunks[i - 1]
        else:
            tail = encoding.decode(prev_tokens[-overlap_tokens:])
        result.append(tail + " " + chunks[i])
    return result


# ---------- Public API --------------------------------------------------------


def chunk_text(
    text: str,
    config: ChunkConfig | None = None,
) -> list[str]:
    """Chunk a raw text string. Returns list of chunk strings (no metadata).

    Used internally and exposed for testing. For production ingestion,
    call chunk_document() instead.
    """
    cfg = config or ChunkConfig()
    encoding = _get_tokenizer(cfg.tokenizer_name)

    sections = _split_sections(text)
    chunks = _pack_pieces(sections, encoding, cfg)
    chunks = _apply_overlap(chunks, encoding, cfg.overlap_tokens)

    # Drop too-small chunks
    chunks = [c for c in chunks if len(encoding.encode(c)) >= cfg.min_tokens]
    return chunks


def chunk_document(
    doc: ExtractedDocument,
    config: ChunkConfig | None = None,
    chunk_id_prefix: str | None = None,
) -> list[Chunk]:
    """Chunk a full ExtractedDocument into Chunk objects with metadata.

    Chunks each ExtractedUnit independently, then concatenates results so
    `chunk_index` is unique across the whole document. Page/sheet/slide
    boundaries are respected — a chunk never spans two units.
    """
    cfg = config or ChunkConfig()
    encoding = _get_tokenizer(cfg.tokenizer_name)
    prefix = chunk_id_prefix or doc.source_path

    all_chunks: list[Chunk] = []
    chunk_index = 0

    for unit in doc.units:
        if not unit.text.strip():
            continue
        unit_chunks = chunk_text(unit.text, cfg)
        for c_text in unit_chunks:
            token_count = len(encoding.encode(c_text))
            chunk_id = f"{prefix}::unit{unit.index}::chunk{chunk_index}"
            metadata: dict[str, str | int] = {
                "source_path": doc.source_path,
                "source_format": doc.source_format,
                "unit_index": unit.index,
                "unit_label": unit.label,
                "chunk_index": chunk_index,
            }
            # Inherit document-level metadata (industry, language, etc.)
            for k, v in doc.metadata.items():
                metadata[f"doc_{k}"] = v
            all_chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    text=c_text,
                    token_count=token_count,
                    metadata=metadata,
                )
            )
            chunk_index += 1

    log.info(
        "chunker.done",
        source_path=doc.source_path,
        source_format=doc.source_format,
        units=len(doc.units),
        chunks=len(all_chunks),
        avg_tokens=int(sum(c.token_count for c in all_chunks) / len(all_chunks))
        if all_chunks
        else 0,
    )

    return all_chunks


# ---------- CLI demo ----------------------------------------------------------


def main() -> None:
    """Quick CLI: `python -m app.ingestion.chunker <path>`.

    Runs extractor + chunker, prints chunk count, token distribution, previews.
    """
    import sys
    from pathlib import Path
    from app.ingestion.extractor import get_extractor

    if len(sys.argv) != 2:
        print("usage: python -m app.ingestion.chunker <path>")
        sys.exit(2)

    path = Path(sys.argv[1])
    extractor = get_extractor(path)
    doc = extractor.extract(path)
    chunks = chunk_document(doc)

    print(f"source:      {doc.source_path}")
    print(f"format:      {doc.source_format}")
    print(f"units:       {len(doc.units)}")
    print(f"chunks:      {len(chunks)}")
    if chunks:
        sizes = [c.token_count for c in chunks]
        print(f"tokens/chunk: min={min(sizes)} avg={sum(sizes)//len(sizes)} max={max(sizes)}")
    print()
    for c in chunks[:3]:
        preview = c.text[:200].replace("\n", " ⏎ ")
        print(f"  [{c.chunk_id}] tokens={c.token_count}")
        print(f"    {preview}{'...' if len(c.text) > 200 else ''}")
    if len(chunks) > 3:
        print(f"  ... and {len(chunks) - 3} more chunks")


if __name__ == "__main__":
    main()