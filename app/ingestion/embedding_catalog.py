"""Embedding models offered when building an Index.

The dimension count is part of the contract, not a hint: Weaviate stores a
fixed-width vector per Index Version, so a model and its dimensions are pinned
into the Embedding Profile and cannot be edited in place — changing either
means building a new Index Version.

Ids are the strings the configured embedding endpoint expects. With
EMBEDDING_BASE_URL pointing at OpenRouter these are provider-prefixed
("openai/..."); against OpenAI directly they are bare ("text-embedding-3-...").
`verified` marks the models confirmed to return vectors through OpenRouter;
the rest are exposed but may require enabling their provider first.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EmbeddingModelChoice:
    """Provides the EmbeddingModelChoice behaviour.

    Attributes:
        id (str).
        label (str).
        dimensions (int).
        provider (str).
        verified (bool).
        note (str).
    """
    id: str
    label: str
    dimensions: int
    provider: str
    verified: bool = True
    note: str = ""


EMBEDDING_MODELS: tuple[EmbeddingModelChoice, ...] = (
    EmbeddingModelChoice(
        id="openai/text-embedding-3-small",
        label="OpenAI text-embedding-3-small",
        dimensions=1536,
        provider="openai",
        note="Fast and inexpensive. Good default for product and support corpora.",
    ),
    EmbeddingModelChoice(
        id="openai/text-embedding-3-large",
        label="OpenAI text-embedding-3-large",
        dimensions=3072,
        provider="openai",
        note="Highest retrieval quality of the OpenAI family; larger vectors, higher cost.",
    ),
    EmbeddingModelChoice(
        id="openai/text-embedding-ada-002",
        label="OpenAI text-embedding-ada-002 (legacy)",
        dimensions=1536,
        provider="openai",
        note="Superseded by the 3-series. Keep only for compatibility with old indexes.",
    ),
    EmbeddingModelChoice(
        id="google/gemini-embedding-001",
        label="Google gemini-embedding-001",
        dimensions=3072,
        provider="google",
        note="Strong multilingual retrieval.",
    ),
    EmbeddingModelChoice(
        id="qwen/qwen3-embedding-8b",
        label="Qwen3 Embedding 8B",
        dimensions=4096,
        provider="qwen",
        verified=False,
        note="Requires enabling its provider in the OpenRouter account.",
    ),
    EmbeddingModelChoice(
        id="qwen/qwen3-embedding-4b",
        label="Qwen3 Embedding 4B",
        dimensions=2560,
        provider="qwen",
        verified=False,
        note="Requires enabling its provider in the OpenRouter account.",
    ),
    EmbeddingModelChoice(
        id="baai/bge-m3",
        label="BAAI bge-m3",
        dimensions=1024,
        provider="baai",
        verified=False,
        note="Requires enabling its provider in the OpenRouter account.",
    ),
    EmbeddingModelChoice(
        id="text-embedding-3-small",
        label="text-embedding-3-small (direct OpenAI endpoint)",
        dimensions=1536,
        provider="openai",
        note="Use when EMBEDDING_BASE_URL is unset or points straight at OpenAI.",
    ),
)

EMBEDDING_MODELS_BY_ID = {choice.id: choice for choice in EMBEDDING_MODELS}

AUTO_EMBEDDING_MODEL = "auto"

# Collection doc_types where retrieving the wrong passage is expensive enough to
# justify the higher-quality (and costlier) embedding model. The doc-type
# catalog derives its "affects embedding choice" flag from this same set, so the
# picker in the UI can never disagree with what select_embedding_model() does.
PRECISION_SENSITIVE_DOC_TYPES: frozenset[str] = frozenset({
    "technical_documentation", "manual", "spec", "specification",
    "policy", "contract", "legal", "regulation", "standard", "research",
})


def embedding_model_catalog() -> list[dict[str, object]]:
    """Compute the embedding model catalog.

    Returns:
        list[dict[str, object]]: The model catalog.
    """
    return [asdict(choice) for choice in EMBEDDING_MODELS]


# Non-ASCII beyond Latin-1 is the cheapest reliable signal that a corpus is not
# plain English/European text, where a multilingual model earns its cost.
def _looks_multilingual(sample: str) -> bool:
    """Internal helper for the looks multilingual step.

    Args:
        sample (str): The sample.

    Returns:
        bool: The multilingual.
    """
    if not sample:
        return False
    exotic = sum(1 for char in sample if ord(char) > 0x24F)
    return exotic > max(20, len(sample) // 200)


def select_embedding_model(
    *,
    doc_types: list[str] | None = None,
    document_count: int = 0,
    total_bytes: int = 0,
    sample_text: str = "",
) -> tuple[EmbeddingModelChoice, str]:
    """Deterministically pick an embedding model for a corpus.

    Zero-token and reproducible — the same corpus always resolves the same way,
    and the resolved concrete model is what gets pinned into the Embedding
    Profile and Index Version. "auto" never reaches storage.

    Returns the choice and a human-readable reason recorded on the profile.
    """
    types = {value.lower() for value in (doc_types or [])}

    if _looks_multilingual(sample_text):
        return (
            EMBEDDING_MODELS_BY_ID["google/gemini-embedding-001"],
            "corpus contains substantial non-Latin text, so a multilingual model was chosen",
        )

    # Large corpora: 3-large costs ~6.5x more per token and doubles vector
    # storage, which rarely pays for itself across a big general corpus.
    large_corpus = document_count > 500 or total_bytes > 250 * 1024 * 1024
    if large_corpus:
        return (
            EMBEDDING_MODELS_BY_ID["openai/text-embedding-3-small"],
            f"large corpus ({document_count} documents), so the cheaper 1536-dimension model was chosen",
        )

    # Precision-sensitive material where a wrong passage is expensive.
    precise = types & PRECISION_SENSITIVE_DOC_TYPES
    if precise:
        return (
            EMBEDDING_MODELS_BY_ID["openai/text-embedding-3-large"],
            f"precision-sensitive document types ({', '.join(sorted(precise))}), so the highest-quality model was chosen",
        )

    return (
        EMBEDDING_MODELS_BY_ID["openai/text-embedding-3-small"],
        "general-purpose corpus, so the balanced default model was chosen",
    )
