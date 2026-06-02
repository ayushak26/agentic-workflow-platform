"""Web search backend for the MCP `search_web` tool.

Mirrors the gateway-stub pattern used for LLM providers: the capability is
real and demonstrable, but the concrete search vendor is pluggable and
defaults to a stub. Swap `_BACKEND` (or set WEB_SEARCH_BACKEND) to a real
provider adapter without touching the MCP server or any workflow.

Each result is normalised to the same shape the RAG layer already speaks
(title, url, snippet, score) so an MCP Agent can treat web hits and document
hits uniformly. This is "web search in RAG form": ranked passages with
provenance, not raw HTML.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Protocol

from app.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str
    score: float  # 0-1 relevance, backend-assigned; stub uses rank decay


class WebSearchBackend(Protocol):
    def search(self, query: str, top_k: int) -> list[WebResult]: ...


class StubWebSearch:
    """Deterministic offline backend. Lets the tool, the MCP wiring, and any
    workflow that calls it be tested end-to-end without a vendor or network.
    Returns clearly-marked synthetic results so a stub run is never mistaken
    for real data."""

    def search(self, query: str, top_k: int) -> list[WebResult]:
        log.info("web_search.stub", query=query, top_k=top_k)
        return [
            WebResult(
                title=f"[STUB RESULT {i+1}] {query[:60]}",
                url=f"https://example.invalid/stub/{i+1}",
                snippet=(
                    "Synthetic web-search result. Replace StubWebSearch with a "
                    "real backend (e.g. an HTTP adapter to a search API) to get "
                    "live results. This text is a placeholder, not a fact."
                ),
                score=round(1.0 - i * (1.0 / max(top_k, 1)), 3),
            )
            for i in range(top_k)
        ]


# --- Backend selection -----------------------------------------------------
# Default is the stub. To wire a real provider, implement a class satisfying
# WebSearchBackend (one `search` method returning WebResult list) in this file
# and select it here or via env. No other file changes.
def _select_backend() -> WebSearchBackend:
    name = os.getenv("WEB_SEARCH_BACKEND", "stub").lower()
    if name == "stub":
        return StubWebSearch()
    # e.g. if name == "tavily": return TavilyWebSearch(api_key=...)
    log.warning("web_search.unknown_backend", requested=name, fallback="stub")
    return StubWebSearch()


_BACKEND: WebSearchBackend = _select_backend()


def search_web(query: str, top_k: int = 8) -> list[dict]:
    """Public entry the MCP server calls. Returns plain dicts (JSON-safe) in
    the same field shape as document retrieval, so results slot into the same
    grounding/citation path."""
    if not query or not query.strip():
        raise ValueError("search_web: empty query")
    top_k = max(1, min(top_k, 20))
    results = _BACKEND.search(query.strip(), top_k)
    return [asdict(r) for r in results]