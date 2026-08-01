"""Bounded clients for documented public database APIs.

The service is created once in ``app.main`` and injected into workflow nodes.
It follows the K-Dense database-lookup contract: explicit endpoints and
parameters, bounded responses, one retry for throttling, and no credentials in
provenance output.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.evidence.database_models import StructuredDatasetQuery


EUROSTAT_JSONSTAT_BASE = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
)


@dataclass(frozen=True)
class DatabaseResponse:
    endpoint: str
    parameters: dict[str, Any]
    raw: bytes
    content_type: str
    headers: dict[str, str]


class PublicDatabaseLookupService:
    """Rate-limited, allow-listed public database transport."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        minimum_interval_seconds: float = 0.5,
    ) -> None:
        self._http_client = http_client
        self._owns_client = http_client is None
        self._minimum_interval_seconds = max(
            0.0,
            minimum_interval_seconds,
        )
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def query_eurostat(
        self,
        query: StructuredDatasetQuery,
        *,
        timeout_seconds: float = 45.0,
        max_response_bytes: int = 20 * 1024 * 1024,
    ) -> DatabaseResponse:
        endpoint = f"{EUROSTAT_JSONSTAT_BASE}/{query.dataset_code}"
        parameter_pairs: list[tuple[str, str]] = [("lang", "en")]
        for field_name in sorted(query.filters):
            parameter_pairs.extend(
                (field_name, value)
                for value in query.filters[field_name]
            )
        if query.start_period and query.end_period:
            # JSON-stat accepts repeated named ``time`` values. A bounded
            # explicit range is expanded only for annual queries; monthly and
            # quarterly ranges must be supplied as exact filter values.
            if (
                len(query.start_period) == 4
                and len(query.end_period) == 4
            ):
                start, end = int(query.start_period), int(query.end_period)
                if end < start or end - start > 100:
                    raise ValueError("invalid or unbounded annual period range")
                parameter_pairs.extend(
                    ("time", str(year)) for year in range(start, end + 1)
                )
            elif "time" not in query.filters:
                raise ValueError(
                    "monthly/quarterly ranges require explicit time filter values"
                )
        elif query.start_period or query.end_period:
            raise ValueError("both start_period and end_period are required")

        parameters = _pairs_for_audit(parameter_pairs)
        client = await self._client(timeout_seconds)
        response: httpx.Response | None = None
        for attempt in range(2):
            await self._rate_limit()
            response = await client.get(
                endpoint,
                params=parameter_pairs,
                headers={
                    "Accept": "application/json",
                    "User-Agent": (
                        "EurskemAI-DatabaseLookup/1.0 "
                        "(reproducible Horizon evidence retrieval)"
                    ),
                },
            )
            if response.status_code not in {429, 503} or attempt == 1:
                break
            retry_after = response.headers.get("retry-after", "1")
            try:
                delay = min(2.0, max(0.0, float(retry_after)))
            except ValueError:
                delay = 1.0
            await asyncio.sleep(delay)
        assert response is not None
        response.raise_for_status()
        raw = response.content
        if len(raw) > max_response_bytes:
            raise ValueError(
                f"database response exceeds {max_response_bytes} bytes"
            )
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type and not raw.lstrip().startswith(b"{"):
            raise ValueError("Eurostat response was not JSON")
        return DatabaseResponse(
            endpoint=endpoint,
            parameters=parameters,
            raw=raw,
            content_type=content_type or "application/json",
            headers={
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"etag", "last-modified", "content-type"}
            },
        )

    async def _client(self, timeout_seconds: float) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        return self._http_client

    async def _rate_limit(self) -> None:
        if not self._minimum_interval_seconds:
            return
        loop = asyncio.get_running_loop()
        async with self._rate_lock:
            elapsed = loop.time() - self._last_request_at
            if elapsed < self._minimum_interval_seconds:
                await asyncio.sleep(self._minimum_interval_seconds - elapsed)
            self._last_request_at = loop.time()


def _pairs_for_audit(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        existing = result.get(key)
        if existing is None:
            result[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            result[key] = [existing, value]
    return result


def get_database_lookup_service() -> PublicDatabaseLookupService:
    return PublicDatabaseLookupService()
