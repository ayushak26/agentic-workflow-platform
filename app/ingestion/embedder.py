"""Embed chunks into vectors using OpenAI.

Defaults to text-embedding-3-small (1536d). Configurable per workflow via
ChunkConfig in Phase 3. Handles batching, retry with exponential backoff,
and rate-limit-aware throughput.
"""
from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.observability.logging import get_logger

log = get_logger(__name__)


# ---------- Config ------------------------------------------------------------


@dataclass
class EmbedderConfig:
    """Tunable embedder parameters."""

    model: str = "text-embedding-3-small"
    dimensions: int = 1536  # default for text-embedding-3-small
    batch_size: int = 100
    max_attempts: int = 5
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0


# ---------- Embedder ----------------------------------------------------------


class Embedder:
    """Async OpenAI embedding client.

    Constructed once and reused. Tests can inject a fake client via the
    `client` argument.
    """

    def __init__(
        self,
        config: EmbedderConfig | None = None,
        client: AsyncOpenAI | None = None,
    ):
        self.config = config or EmbedderConfig()
        if client is not None:
            self._client = client
        else:
            if not settings.openai_api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set; embedder cannot create a default client"
                )
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.external_request_timeout_seconds,
                max_retries=0,
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts. Returns vectors in the same order.

        Handles batching internally — caller passes the full list, embedder
        breaks it into batches of `config.batch_size`.
        """
        if not texts:
            return []

        all_vectors: list[list[float]] = []
        cfg = self.config

        for batch_start in range(0, len(texts), cfg.batch_size):
            batch = texts[batch_start : batch_start + cfg.batch_size]
            log.debug(
                "embedder.batch_start",
                batch_index=batch_start // cfg.batch_size,
                size=len(batch),
            )
            vectors = await self._embed_batch_with_retry(batch)
            all_vectors.extend(vectors)

        log.info(
            "embedder.done",
            total_texts=len(texts),
            total_vectors=len(all_vectors),
            model=cfg.model,
        )
        return all_vectors

    async def _embed_batch_with_retry(self, batch: list[str]) -> list[list[float]]:
        """Single batch with tenacity-managed retry."""
        cfg = self.config

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(cfg.max_attempts),
            wait=wait_exponential(
                multiplier=cfg.initial_backoff_seconds,
                max=cfg.max_backoff_seconds,
            ),
            retry=retry_if_exception_type(
                (RateLimitError, APIConnectionError, APITimeoutError)
            ),
            reraise=True,
        ):
            with attempt:
                response = await self._client.embeddings.create(
                    model=cfg.model,
                    input=batch,
                )
                return [item.embedding for item in response.data]

        raise RuntimeError("retry loop exited without returning (should not happen)")


# ---------- Module-level singleton -------------------------------------------

_default_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Lazily-constructed module-level embedder."""
    global _default_embedder
    if _default_embedder is None:
        _default_embedder = Embedder()
    return _default_embedder
