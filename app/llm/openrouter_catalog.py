"""Live OpenRouter model catalog, sourced directly from OpenRouter's own `GET /v1/models`.

Separate from `app/llm/catalog.py`'s `MODEL_CATALOG` (the curated, hand-approved list of
direct-provider/local models used for compliance-gated data-class restrictions). OpenRouter's
catalog has ~400-500 entries and changes continuously as OpenRouter adds/removes upstream
models, so it is deliberately NOT baked into the static catalog — this module fetches it live
and caches the result in-process with a short TTL, so the Builder UI's model picker reflects
what's actually available right now without hardcoding a snapshot.

`GET /v1/models` is OpenRouter's public catalog listing — no API key required, unlike chat
completions. Real response shape confirmed live (2026-08-13): `pricing.prompt`/
`pricing.completion` are per-token USD strings (not per-million, and not the `{input, output}`
per-million shape a proxy might synthesize), `id` is bare (e.g. "openai/gpt-4o-mini", no
"openrouter/" prefix — that prefix is Eurskem's own gateway-selection convention, added here,
see app/llm/registry.py's _PREFIX_ROUTES and app/llm/openrouter_gw.py).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

OPENROUTER_MODEL_ID_PATTERN = re.compile(r"^openrouter/[^/\s]+(?:/[^/\s]+)+$")

_CACHE_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class OpenRouterModelInfo:
    id: str
    display_name: str
    context_length: int | None
    max_output_tokens: int | None
    input_usd_per_million: float | None
    output_usd_per_million: float | None
    supports_tool_calling: bool
    supports_vision: bool
    supports_reasoning: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "provider": "openrouter",
            "context_length": self.context_length,
            "max_output_tokens": self.max_output_tokens,
            "input_usd_per_million": self.input_usd_per_million,
            "output_usd_per_million": self.output_usd_per_million,
            "supports_tool_calling": self.supports_tool_calling,
            "supports_vision": self.supports_vision,
            "supports_reasoning": self.supports_reasoning,
        }


def is_openrouter_model_id(value: str) -> bool:
    """Structural check only (no live lookup) — used by preflight, which must stay
    synchronous and network-free. OpenRouter is the authoritative source and will reject a
    syntactically-valid-but-nonexistent model at dispatch time with a clear error."""
    return bool(OPENROUTER_MODEL_ID_PATTERN.fullmatch(value))


def _price_per_million(raw: Any) -> float | None:
    """OpenRouter reports pricing as a per-token USD string (e.g. "0.00000015")."""
    if raw is None:
        return None
    try:
        return float(raw) * 1_000_000
    except (TypeError, ValueError):
        return None


def _to_model_info(entry: dict[str, Any]) -> OpenRouterModelInfo | None:
    bare_id = entry.get("id")
    if not isinstance(bare_id, str) or not bare_id:
        return None
    pricing = entry.get("pricing") or {}
    architecture = entry.get("architecture") or {}
    top_provider = entry.get("top_provider") or {}
    supported_params = entry.get("supported_parameters") or []
    return OpenRouterModelInfo(
        id=f"openrouter/{bare_id}",
        display_name=entry.get("name") or bare_id,
        context_length=entry.get("context_length"),
        max_output_tokens=top_provider.get("max_completion_tokens"),
        input_usd_per_million=_price_per_million(pricing.get("prompt")),
        output_usd_per_million=_price_per_million(pricing.get("completion")),
        supports_tool_calling="tools" in supported_params,
        supports_vision="image" in (architecture.get("input_modalities") or []),
        supports_reasoning=(
            "reasoning" in supported_params or "reasoning_effort" in supported_params
        ),
    )


async def _fetch_raw_catalog(client: httpx.AsyncClient | None) -> list[dict[str, Any]]:
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        timeout=settings.openrouter_request_timeout_seconds,
    )
    try:
        response = await active_client.get("/models")
        response.raise_for_status()
        body = response.json()
    finally:
        if owns_client:
            await active_client.aclose()
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, list) else []


class OpenRouterCatalogCache:
    """Process-wide TTL cache. A dedicated instance (not module-level globals) so tests can
    construct isolated caches instead of monkeypatching shared state."""

    def __init__(self, *, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._models: tuple[OpenRouterModelInfo, ...] = ()
        self._fetched_at: float = 0.0

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) >= self._ttl_seconds

    async def get_models(
        self, *, client: httpx.AsyncClient | None = None, force_refresh: bool = False
    ) -> tuple[OpenRouterModelInfo, ...]:
        if force_refresh or self._is_stale():
            raw_models = await _fetch_raw_catalog(client)
            parsed = (_to_model_info(entry) for entry in raw_models)
            self._models = tuple(model for model in parsed if model is not None)
            self._fetched_at = time.monotonic()
        return self._models

    async def search(
        self,
        query: str | None = None,
        *,
        limit: int = 50,
        client: httpx.AsyncClient | None = None,
    ) -> list[OpenRouterModelInfo]:
        models = await self.get_models(client=client)
        if query:
            needle = query.strip().lower()
            models = tuple(
                model
                for model in models
                if needle in model.id.lower() or needle in model.display_name.lower()
            )
        return list(models[:limit])


_default_cache = OpenRouterCatalogCache()


def get_default_cache() -> OpenRouterCatalogCache:
    return _default_cache
