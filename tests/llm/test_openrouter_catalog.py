"""Tests for app/llm/openrouter_catalog.py — the live, TTL-cached OpenRouter model catalog.

Uses httpx.MockTransport (no live network calls) so these run standalone; the real
OpenRouter integration is separately verified live.
"""
from __future__ import annotations

import httpx

from app.llm.openrouter_catalog import (
    OpenRouterCatalogCache,
    is_openrouter_model_id,
)

# Real OpenRouter /v1/models response shape (confirmed live 2026-08-13): bare ids (no
# "openrouter/" prefix — that's added by _to_model_info), pricing as per-token USD strings,
# capability info under architecture.input_modalities / supported_parameters.
RAW_CATALOG = [
    {
        "id": "openai/gpt-4o-mini",
        "name": "OpenAI: GPT-4o-mini",
        "context_length": 128000,
        "top_provider": {"max_completion_tokens": 16384},
        "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "temperature"],
    },
    {
        "id": "anthropic/claude-3-haiku",
        "name": "Anthropic: Claude 3 Haiku",
        "context_length": 200000,
        "pricing": {"prompt": "0.00000025", "completion": "0.00000125"},
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["tools"],
    },
]


def _client(models=None, *, calls: list[int] | None = None) -> httpx.AsyncClient:
    payload = models if models is not None else RAW_CATALOG

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(len(calls) + 1)
        return httpx.Response(200, json={"data": payload})

    return httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(handler)
    )


def test_is_openrouter_model_id_requires_vendor_and_model_segments():
    assert is_openrouter_model_id("openrouter/openai/gpt-4o-mini")
    assert is_openrouter_model_id("openrouter/openai/gpt-4o-mini:batch")
    assert not is_openrouter_model_id("openrouter/broken")
    assert not is_openrouter_model_id("openai/gpt-4o-mini")
    assert not is_openrouter_model_id("auto")


async def test_get_models_adds_the_openrouter_prefix_to_bare_ids():
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client() as client:
        models = await cache.get_models(client=client)

    ids = {model.id for model in models}
    assert ids == {"openrouter/openai/gpt-4o-mini", "openrouter/anthropic/claude-3-haiku"}


async def test_get_models_parses_pricing_and_capabilities():
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client() as client:
        models = await cache.get_models(client=client)

    gpt = next(m for m in models if m.id == "openrouter/openai/gpt-4o-mini")
    assert gpt.display_name == "OpenAI: GPT-4o-mini"
    assert gpt.context_length == 128000
    assert gpt.max_output_tokens == 16384
    assert gpt.input_usd_per_million == 0.15
    assert gpt.output_usd_per_million == 0.6
    assert gpt.supports_tool_calling is True
    assert gpt.supports_vision is True
    assert gpt.supports_reasoning is False


async def test_get_models_defaults_missing_metadata_to_none_or_false():
    haiku_only = [{"id": "anthropic/claude-3-haiku", "name": "Claude 3 Haiku"}]
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client(haiku_only) as client:
        models = await cache.get_models(client=client)

    model = models[0]
    assert model.context_length is None
    assert model.input_usd_per_million is None
    assert model.supports_tool_calling is False


async def test_cache_reuses_result_within_ttl():
    calls: list[int] = []
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client(calls=calls) as client:
        await cache.get_models(client=client)
        await cache.get_models(client=client)

    assert len(calls) == 1


async def test_cache_refetches_after_ttl_expires():
    calls: list[int] = []
    cache = OpenRouterCatalogCache(ttl_seconds=0.0)
    async with _client(calls=calls) as client:
        await cache.get_models(client=client)
        await cache.get_models(client=client)

    assert len(calls) == 2


async def test_force_refresh_bypasses_ttl():
    calls: list[int] = []
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client(calls=calls) as client:
        await cache.get_models(client=client)
        await cache.get_models(client=client, force_refresh=True)

    assert len(calls) == 2


async def test_search_filters_by_id_or_display_name_case_insensitively():
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client() as client:
        by_id = await cache.search("gpt-4o-mini", client=client)
        by_name = await cache.search("CLAUDE 3", client=client)

    assert [m.id for m in by_id] == ["openrouter/openai/gpt-4o-mini"]
    assert [m.id for m in by_name] == ["openrouter/anthropic/claude-3-haiku"]


async def test_search_respects_limit():
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client() as client:
        results = await cache.search(None, limit=1, client=client)

    assert len(results) == 1


async def test_search_with_no_query_returns_everything_up_to_limit():
    cache = OpenRouterCatalogCache(ttl_seconds=600)
    async with _client() as client:
        results = await cache.search(None, limit=50, client=client)

    assert len(results) == 2
