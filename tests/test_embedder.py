"""Tests for the embedder. Uses a fake OpenAI client to avoid live API calls."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.embedder import Embedder, EmbedderConfig


@dataclass
class _FakeEmbeddingItem:
    embedding: list[float]


@dataclass
class _FakeEmbeddingResponse:
    data: list[_FakeEmbeddingItem]


def _make_fake_client(vectors_to_return: list[list[float]]) -> MagicMock:
    """Create a mock AsyncOpenAI client that returns the given vectors."""
    client = MagicMock()
    client.embeddings = MagicMock()
    client.embeddings.create = AsyncMock(
        return_value=_FakeEmbeddingResponse(
            data=[_FakeEmbeddingItem(embedding=v) for v in vectors_to_return]
        )
    )
    return client


@pytest.mark.asyncio
async def test_embed_empty_returns_empty():
    client = _make_fake_client([])
    embedder = Embedder(client=client)
    assert await embedder.embed([]) == []
    client.embeddings.create.assert_not_called()


@pytest.mark.asyncio
async def test_embed_single_batch():
    vectors = [[0.1, 0.2], [0.3, 0.4]]
    client = _make_fake_client(vectors)
    embedder = Embedder(client=client)
    result = await embedder.embed(["text one", "text two"])
    assert result == vectors


@pytest.mark.asyncio
async def test_embed_multiple_batches():
    """Texts beyond batch_size should be split into multiple API calls."""
    cfg = EmbedderConfig(batch_size=2)
    # Two batches of 2, returning [0.1]*1536-like fakes for simplicity
    fake_vec = [0.42] * 4
    client = MagicMock()
    client.embeddings = MagicMock()
    client.embeddings.create = AsyncMock(
        side_effect=[
            _FakeEmbeddingResponse(data=[_FakeEmbeddingItem(fake_vec), _FakeEmbeddingItem(fake_vec)]),
            _FakeEmbeddingResponse(data=[_FakeEmbeddingItem(fake_vec)]),
        ]
    )
    embedder = Embedder(config=cfg, client=client)
    result = await embedder.embed(["a", "b", "c"])
    assert len(result) == 3
    assert client.embeddings.create.call_count == 2


@pytest.mark.asyncio
async def test_embed_retries_on_rate_limit():
    """Tenacity should retry on RateLimitError and eventually succeed."""
    from openai import RateLimitError

    cfg = EmbedderConfig(max_attempts=3, initial_backoff_seconds=0.01)
    fake_vec = [0.1, 0.2]
    success_response = _FakeEmbeddingResponse(data=[_FakeEmbeddingItem(fake_vec)])

    # Fail twice with RateLimitError, succeed on third
    rate_limit_error = RateLimitError(
        message="rate limited",
        response=MagicMock(status_code=429, headers={}),
        body=None,
    )

    client = MagicMock()
    client.embeddings = MagicMock()
    client.embeddings.create = AsyncMock(
        side_effect=[rate_limit_error, rate_limit_error, success_response]
    )

    embedder = Embedder(config=cfg, client=client)
    result = await embedder.embed(["recover"])
    assert result == [fake_vec]
    assert client.embeddings.create.call_count == 3