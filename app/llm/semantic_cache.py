"""Tenant-scoped Redis semantic cache for deterministic text completions.

Invariant: this module must only ever be reached with already-tokenized
`system`/`user` text (confidential entities already replaced with
placeholders). Enforcement lives in RegistryLLMGateway's public complete()
wrapper (app/llm/registry.py), which brackets the ENTIRE method body of the
renamed _complete_impl — including this cache's get()/put() calls and the
embedder call inside get() — not just the underlying provider call. A future
refactor that lets raw text reach get()/put() directly (e.g. calling this
cache from a new code path) would reopen the leak this design closes.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.observability import metrics
from app.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class CacheLookup:
    """Provides the CacheLookup behaviour.

    Attributes:
        hit (bool).
        response (dict[str, Any] | None).
        similarity (float | None).
        query_embedding (list[float] | None).
    """
    hit: bool
    response: dict[str, Any] | None = None
    similarity: float | None = None
    query_embedding: list[float] | None = None


class SemanticLLMCache:
    """Small-scope semantic cache backed by Redis.

    Entries are isolated by authenticated session, requested model, system
    prompt, sampling parameters, and token limit. Only the newest bounded set
    is compared in Python, which is an appropriate trade-off for this
    single-VPS proposal platform and avoids another public vector index.
    """

    def __init__(self, redis, embedder) -> None:
        """Initialize the SemanticLLMCache.

        Args:
            redis: Redis client.
            embedder: The embedder.
        """
        self._redis = redis
        self._embedder = embedder

    async def probe(self) -> bool:
        """Probe the result.

        Returns:
            bool: The result.
        """
        return bool(await self._redis.ping())

    async def get(
        self,
        *,
        session_id: str,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> CacheLookup:
        """Return the result.

        Args:
            session_id (str): Session scope the record belongs to.
            model (str): Model name.
            system (str): The system.
            user (str): Authenticated current user.
            temperature (float): The temperature.
            max_tokens (int): The max tokens.

        Returns:
            CacheLookup: The result.
        """
        scope = self._scope(
            session_id=session_id,
            model=model,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        exact_key = self._entry_key(scope, user)
        try:
            exact = await self._redis.get(exact_key)
            if exact:
                response = json.loads(exact)
                response.pop("_embedding", None)
                metrics.LLM_CACHE.labels(status="hit").inc()
                return CacheLookup(
                    hit=True,
                    response=response,
                    similarity=1.0,
                )

            query_embedding = (await self._embedder.embed([user]))[0]
            keys = await self._redis.zrevrange(
                self._index_key(scope),
                0,
                settings.semantic_cache_max_entries_per_scope - 1,
            )
            if not keys:
                metrics.LLM_CACHE.labels(status="miss").inc()
                return CacheLookup(hit=False, query_embedding=query_embedding)

            payloads = await self._redis.mget(keys)
            best: tuple[float, dict[str, Any]] | None = None
            for raw in payloads:
                if not raw:
                    continue
                item = json.loads(raw)
                candidate = item.pop("_embedding", None)
                if not isinstance(candidate, list):
                    continue
                similarity = _cosine(query_embedding, candidate)
                if best is None or similarity > best[0]:
                    best = (similarity, item)

            if (
                best is not None
                and best[0] >= settings.semantic_cache_similarity_threshold
            ):
                metrics.LLM_CACHE.labels(status="hit").inc()
                return CacheLookup(
                    hit=True,
                    response=best[1],
                    similarity=best[0],
                    query_embedding=query_embedding,
                )
            metrics.LLM_CACHE.labels(status="miss").inc()
            return CacheLookup(hit=False, query_embedding=query_embedding)
        except Exception as exc:
            metrics.LLM_CACHE.labels(status="error").inc()
            log.warning(
                "semantic_cache.lookup_failed",
                error_type=type(exc).__name__,
            )
            return CacheLookup(hit=False)

    async def put(
        self,
        *,
        session_id: str,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        response: dict[str, Any],
        query_embedding: list[float] | None,
    ) -> None:
        """Store the result.

        Args:
            session_id (str): Session scope the record belongs to.
            model (str): Model name.
            system (str): The system.
            user (str): Authenticated current user.
            temperature (float): The temperature.
            max_tokens (int): The max tokens.
            response (dict[str, Any]): Outgoing FastAPI response.
            query_embedding (list[float] | None): The query embedding.
        """
        scope = self._scope(
            session_id=session_id,
            model=model,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            embedding = query_embedding
            if embedding is None:
                embedding = (await self._embedder.embed([user]))[0]
            entry_key = self._entry_key(scope, user)
            payload = dict(response)
            payload["_embedding"] = embedding
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.set(
                entry_key,
                json.dumps(payload, separators=(",", ":")),
                ex=settings.semantic_cache_ttl_seconds,
            )
            pipeline.zadd(self._index_key(scope), {entry_key: time.time()})
            pipeline.expire(
                self._index_key(scope),
                settings.semantic_cache_ttl_seconds,
            )
            await pipeline.execute()
            await self._prune(scope)
        except Exception as exc:
            metrics.LLM_CACHE.labels(status="error").inc()
            log.warning(
                "semantic_cache.store_failed",
                error_type=type(exc).__name__,
            )

    async def _prune(self, scope: str) -> None:
        """Prune the result.

        Args:
            scope (str): Session scope the record belongs to.
        """
        index = self._index_key(scope)
        count = await self._redis.zcard(index)
        extra = int(count) - settings.semantic_cache_max_entries_per_scope
        if extra <= 0:
            return
        stale = await self._redis.zrange(index, 0, extra - 1)
        if stale:
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.zrem(index, *stale)
            pipeline.delete(*stale)
            await pipeline.execute()

    @staticmethod
    def _scope(
        *,
        session_id: str,
        model: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Internal helper for the scope step.

        Args:
            session_id (str): Session scope the record belongs to.
            model (str): Model name.
            system (str): The system.
            temperature (float): The temperature.
            max_tokens (int): The max tokens.

        Returns:
            str: The result.
        """
        raw = "\0".join(
            (
                session_id,
                model,
                hashlib.sha256(system.encode("utf-8")).hexdigest(),
                f"{temperature:.4f}",
                str(max_tokens),
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _entry_key(scope: str, user: str) -> str:
        """Internal helper for the entry key step.

        Args:
            scope (str): Session scope the record belongs to.
            user (str): Authenticated current user.

        Returns:
            str: The key.
        """
        prompt_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()
        return f"awp:semcache:entry:{scope}:{prompt_hash}"

    @staticmethod
    def _index_key(scope: str) -> str:
        """Internal helper for the index key step.

        Args:
            scope (str): Session scope the record belongs to.

        Returns:
            str: The key.
        """
        return f"awp:semcache:index:{scope}"


def _cosine(left: list[float], right: list[float]) -> float:
    """Internal helper for the cosine step.

    Args:
        left (list[float]): The left.
        right (list[float]): The right.

    Returns:
        float: The result.
    """
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
