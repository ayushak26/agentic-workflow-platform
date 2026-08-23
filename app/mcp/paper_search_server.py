"""Credential-aware launcher for the pinned paper-search-mcp server.

paper-search-mcp 0.1.4 has a few provider-side bugs this launcher patches at
import time, before the upstream server creates its singleton searchers. The
rest of the MCP implementation remains upstream-owned.

1. It does not send OpenAlex's ``api_key`` query parameter (fixed here by
   injecting it into the connector's requests.Session).
2. Its OpenAIRE legacy fallback (``OpenAiresearcher.search``'s second try
   block, `/search/publications`) always sends the query under a `query`
   parameter — but that endpoint has never supported it. It only accepts
   `keywords` (confirmed directly against the live API: even a plain query
   string 400s with "Parameter query is not supported. The supported
   parameters are: ... keywords ..."). Patched here by renaming the
   parameter for that one endpoint.
3. That same OpenAIRE legacy fallback then does
   ``data.get('response', {}).get('results', {}).get('result', [])``. The
   endpoint sometimes returns `"response": null` (or a null `results`) for
   an empty/edge-case result set instead of omitting the key — and
   `dict.get(key, default)` only falls back to `default` when the key is
   *absent*, not when it's present with value `None`. That raises
   `'NoneType' object has no attribute 'get'`, logged (and swallowed) as
   "OpenAIRE API request error". Patched here by normalizing the response
   JSON so `response`/`results` are never `None`.
4. Its Semantic Scholar connector does ``response.json()["data"]``
   unconditionally. The Semantic Scholar Graph API omits the `data` key
   entirely (not an empty list) when a query's total is 0, which raises an
   uncaught-looking `KeyError` for what is actually a normal empty result.
   Patched here by wrapping the response so `.json()` always includes
   `data`, defaulting to `[]`.
5. Our keyed Semantic Scholar tier is capped at 1 request/second, *cumulative
   across all endpoints* (per S2's own approval email) — burst straight past
   that and every subsequent call in the burst 429s. The connector only
   reacts to 429s after the fact (a short backoff-and-retry); nothing paces
   requests going in. Patched here with a process-wide minimum interval
   between calls, enforced before each request rather than after a rejection.
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _prepare_source_checkout() -> None:
    """Internal helper for the prepare source checkout step."""
    value = os.getenv("PAPER_SEARCH_MCP_SOURCE_PATH", "").strip()
    if not value:
        return
    path = Path(value).expanduser().resolve()
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def inject_openalex_api_key(searcher: Any, api_key: str) -> bool:
    """Inject the key into a searcher session without logging the secret."""

    value = api_key.strip()
    session = getattr(searcher, "session", None)
    params = getattr(session, "params", None)
    if not value or params is None:
        return False
    params.update({"api_key": value})
    return True


def _normalize_openaire_legacy_json(payload: Any) -> Any:
    """Guard against `"response": null` / `"results": null` in the OpenAIRE
    legacy search payload, which otherwise crashes the caller's
    `data.get('response', {}).get('results', {}).get('result', [])` chain
    (a present `None` value bypasses `dict.get`'s default)."""

    if not isinstance(payload, dict):
        return payload
    response = payload.get("response") or {}
    results = response.get("results") or {}
    results.setdefault("result", [])
    response["results"] = results
    payload["response"] = response
    return payload


class _JsonNullSafeOpenAireResponse:
    """Wraps a requests.Response so `.json()` never nests `None` under
    `response`/`results` for the OpenAIRE legacy search endpoint."""

    def __init__(self, response: Any) -> None:
        """Initialize the _JsonNullSafeOpenAireResponse.

        Args:
            response (Any): Outgoing FastAPI response.
        """
        self._response = response

    def __getattr__(self, name: str) -> Any:
        """Implement the ``__getattr__`` protocol.

        Args:
            name (str): Workflow or resource name.

        Returns:
            Any: The result.
        """
        return getattr(self._response, name)

    def json(self, *args: Any, **kwargs: Any) -> Any:
        """Compute the json.

        Args:
            *args (Any): Positional arguments.
            **kwargs (Any): Keyword arguments.

        Returns:
            Any: The result.
        """
        return _normalize_openaire_legacy_json(self._response.json(*args, **kwargs))


def patch_openaire_query_param(module_name: str = "paper_search_mcp.academic_platforms.openaire") -> None:
    """Rename the legacy fallback's `query` param to the supported `keywords`,
    and normalize its response so a null `response`/`results` doesn't raise
    in the caller's `.get()` chain."""

    openaire = importlib.import_module(module_name)
    original_get = openaire.OpenAiresearcher._get

    def patched_get(self, url, **kwargs):
        """Compute the patched get.

        Args:
            url: Target URL.
            **kwargs: Keyword arguments.
        """
        is_legacy_search = url.rstrip("/").endswith("/search/publications")
        params = kwargs.get("params")
        if (
            is_legacy_search
            and isinstance(params, dict)
            and "query" in params
            and "keywords" not in params
        ):
            params = dict(params)
            params["keywords"] = params.pop("query")
            kwargs = {**kwargs, "params": params}
        response = original_get(self, url, **kwargs)
        if is_legacy_search:
            return _JsonNullSafeOpenAireResponse(response)
        return response

    openaire.OpenAiresearcher._get = patched_get


class _JsonDefaultsDataKey:
    """Wraps a requests.Response so `.json()` always has a `data` key."""

    def __init__(self, response: Any) -> None:
        """Initialize the _JsonDefaultsDataKey.

        Args:
            response (Any): Outgoing FastAPI response.
        """
        self._response = response

    def __getattr__(self, name: str) -> Any:
        """Implement the ``__getattr__`` protocol.

        Args:
            name (str): Workflow or resource name.

        Returns:
            Any: The result.
        """
        return getattr(self._response, name)

    def json(self, *args: Any, **kwargs: Any) -> Any:
        """Compute the json.

        Args:
            *args (Any): Positional arguments.
            **kwargs (Any): Keyword arguments.

        Returns:
            Any: The result.
        """
        payload = self._response.json(*args, **kwargs)
        if isinstance(payload, dict):
            payload.setdefault("data", [])
        return payload


_semantic_throttle_lock = threading.Lock()
_semantic_last_call_at = 0.0


def _wait_for_semantic_scholar_rate_limit(min_interval_seconds: float) -> None:
    """Block until at least `min_interval_seconds` has passed since the last
    call. A process-wide lock + timestamp is enough here: paper-search-mcp's
    connectors are synchronous `requests` calls, and MCP tool calls from this
    process are the only thing that can hit our keyed S2 quota."""

    global _semantic_last_call_at
    with _semantic_throttle_lock:
        now = time.monotonic()
        wait = min_interval_seconds - (now - _semantic_last_call_at)
        if wait > 0:
            time.sleep(wait)
        _semantic_last_call_at = time.monotonic()


def patch_semantic_scholar_empty_results(
    module_name: str = "paper_search_mcp.academic_platforms.semantic",
    min_interval_seconds: float = 1.1,
) -> None:
    """Make zero-result Semantic Scholar responses parse instead of KeyError,
    and pace requests to stay under the 1 req/s cumulative keyed rate limit.

    `min_interval_seconds` defaults to slightly over 1s: the approval email's
    limit is exactly 1/s, and it explicitly says "please set your rate limit
    to below this threshold" — a small margin avoids sitting exactly on the
    boundary against clock/network jitter.
    """

    semantic = importlib.import_module(module_name)
    original_request_api = semantic.SemanticSearcher.request_api

    def patched_request_api(self, path, params):
        """Compute the patched request api.

        Args:
            path: Filesystem path.
            params: The params.
        """
        _wait_for_semantic_scholar_rate_limit(min_interval_seconds)
        result = original_request_api(self, path, params)
        if not isinstance(result, dict) and hasattr(result, "json"):
            return _JsonDefaultsDataKey(result)
        return result

    semantic.SemanticSearcher.request_api = patched_request_api


def main() -> None:
    """Compute the main."""
    _prepare_source_checkout()
    module_name = (
        os.getenv("PAPER_SEARCH_MCP_MODULE", "").strip()
        or "paper_search_mcp.server"
    )
    upstream = importlib.import_module(module_name)

    key = (
        os.getenv("PAPER_SEARCH_MCP_OPENALEX_API_KEY", "")
        or os.getenv("OPENALEX_API_KEY", "")
    )
    inject_openalex_api_key(upstream.openalex_searcher, key)
    patch_openaire_query_param()
    patch_semantic_scholar_empty_results()
    upstream.main()


if __name__ == "__main__":
    main()
