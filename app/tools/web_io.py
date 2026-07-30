"""Live web-search providers with an auditable fallback policy."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

import httpx
from openai import AsyncOpenAI

from app.config import Settings, settings
from app.observability.logging import get_logger

log = get_logger(__name__)

SearchProvider = Literal["auto", "tavily", "openai", "kimi", "stub"]
_URL_RE = re.compile(r"https?://[^\s<>()\]\"']+")


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str
    score: float
    status: str = "candidate_only"


@dataclass(frozen=True)
class WebSearchResponse:
    query: str
    requested_provider: str
    actual_provider: str
    results: list[WebResult]
    fallback_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class TavilyQuotaExhausted(RuntimeError):
    """Tavily cannot serve the request because quota/rate is exhausted."""


class WebSearchService:
    def __init__(
        self,
        app_settings: Settings = settings,
        *,
        http_client: Any | None = None,
        openai_client: Any | None = None,
        kimi_client: Any | None = None,
    ) -> None:
        self.settings = app_settings
        self._http_client = http_client
        self._openai_client = openai_client
        self._kimi_client = kimi_client

    def available_providers(self) -> set[str]:
        providers: set[str] = set()
        if self.settings.tavily_api_key.strip():
            providers.add("tavily")
        if self.settings.openai_api_key.strip():
            providers.add("openai")
        if self.settings.moonshot_api_key.strip():
            providers.add("kimi")
        if self.settings.web_search_backend == "stub":
            providers.add("stub")
        return providers

    def resolve_provider(self, requested: SearchProvider) -> str:
        """Resolve Auto without making a provider request."""

        return self._select_provider(requested)

    async def search(
        self,
        query: str,
        *,
        provider: SearchProvider = "auto",
        top_k: int = 8,
        fallback_to_openai: bool = True,
    ) -> WebSearchResponse:
        query = query.strip()
        if not query:
            raise ValueError("web search query cannot be empty")
        top_k = max(1, min(int(top_k), 20))
        requested = provider
        selected = self._select_provider(provider)

        if selected == "tavily":
            try:
                results = await self._search_tavily(query, top_k)
                return WebSearchResponse(
                    query=query,
                    requested_provider=requested,
                    actual_provider="tavily",
                    results=results,
                )
            except TavilyQuotaExhausted as exc:
                if not fallback_to_openai:
                    raise
                if not self.settings.openai_api_key.strip():
                    raise RuntimeError(
                        "Tavily quota is exhausted and OpenAI fallback is "
                        "not configured"
                    ) from exc
                fallback_reason = str(exc)
                response = await self._search_openai(query, top_k)
                return WebSearchResponse(
                    query=query,
                    requested_provider=requested,
                    actual_provider="openai",
                    results=response.results,
                    fallback_reason=fallback_reason,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
        if selected == "openai":
            response = await self._search_openai(query, top_k)
            return WebSearchResponse(
                query=query,
                requested_provider=requested,
                actual_provider="openai",
                results=response.results,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
        if selected == "kimi":
            response = await self._search_kimi(query, top_k)
            return WebSearchResponse(
                query=query,
                requested_provider=requested,
                actual_provider="kimi",
                results=response.results,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
        if selected == "stub":
            return WebSearchResponse(
                query=query,
                requested_provider=requested,
                actual_provider="stub",
                results=self._stub_results(query, top_k),
            )
        raise RuntimeError(f"unsupported web-search provider: {selected}")

    def _select_provider(self, requested: SearchProvider) -> str:
        if requested != "auto":
            return requested
        configured = self.settings.web_search_backend
        if configured != "auto":
            return configured
        available = self.available_providers()
        for candidate in ("tavily", "openai", "kimi"):
            if candidate in available:
                return candidate
        raise RuntimeError(
            "Auto web search has no configured provider; add Tavily, OpenAI, "
            "or Moonshot credentials"
        )

    async def _search_tavily(
        self,
        query: str,
        top_k: int,
    ) -> list[WebResult]:
        api_key = self.settings.tavily_api_key.strip()
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured")
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": top_k,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        if self._http_client is not None:
            response = await self._http_client.post(
                "https://api.tavily.com/search",
                json=payload,
                headers=headers,
            )
        else:
            async with httpx.AsyncClient(
                timeout=self.settings.external_request_timeout_seconds,
            ) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json=payload,
                    headers=headers,
                )
        if response.status_code in {429, 432}:
            raise TavilyQuotaExhausted(
                f"Tavily returned HTTP {response.status_code}"
            )
        # Authentication, validation, and other request errors must remain
        # visible; they are not evidence that quota is exhausted.
        response.raise_for_status()
        body = response.json()
        results: list[WebResult] = []
        for index, item in enumerate(body.get("results") or []):
            results.append(
                WebResult(
                    title=str(item.get("title") or "Untitled result"),
                    url=str(item.get("url") or ""),
                    snippet=str(
                        item.get("content") or item.get("snippet") or ""
                    ),
                    score=float(
                        item.get("score")
                        if item.get("score") is not None
                        else _rank_score(index, top_k)
                    ),
                )
            )
        return results[:top_k]

    async def _search_openai(
        self,
        query: str,
        top_k: int,
    ) -> WebSearchResponse:
        key = self.settings.openai_api_key.strip()
        if self._openai_client is None and not key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        client = self._openai_client or AsyncOpenAI(
            api_key=key,
            max_retries=0,
            timeout=self.settings.llm_request_timeout_seconds,
        )
        response = await client.responses.create(
            model=self.settings.openai_web_search_model,
            tools=[{"type": "web_search"}],
            input=(
                f"Search the web for: {query}\n"
                f"Return a concise sourced answer using at most {top_k} "
                "distinct sources."
            ),
        )
        text = str(getattr(response, "output_text", "") or "")
        payload = (
            response.model_dump(mode="json")
            if hasattr(response, "model_dump")
            else response
        )
        citations = _url_citations(payload)
        results = [
            WebResult(
                title=item["title"] or f"Source {index + 1}",
                url=item["url"],
                snippet=text,
                score=_rank_score(index, max(len(citations), 1)),
            )
            for index, item in enumerate(citations[:top_k])
        ]
        if not results and text:
            results = [
                WebResult(
                    title="OpenAI web-search response",
                    url="",
                    snippet=text,
                    score=1.0,
                )
            ]
        input_tokens, output_tokens = _response_usage(response)
        return WebSearchResponse(
            query=query,
            requested_provider="openai",
            actual_provider="openai",
            results=results,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _search_kimi(
        self,
        query: str,
        top_k: int,
    ) -> WebSearchResponse:
        key = self.settings.moonshot_api_key.strip()
        if self._kimi_client is None and not key:
            raise RuntimeError("LOCAL_KIMI_API_KEY is not configured")
        client = self._kimi_client or AsyncOpenAI(
            api_key=key,
            base_url=self.settings.kimi_api_base_url,
            max_retries=0,
            timeout=self.settings.llm_request_timeout_seconds,
        )
        tools = [
            {
                "type": "builtin_function",
                "function": {"name": "$web_search"},
            }
        ]
        messages: list[Any] = [
            {
                "role": "system",
                "content": (
                    "Use web search and give a concise answer with source URLs. "
                    f"Use no more than {top_k} distinct sources."
                ),
            },
            {"role": "user", "content": query},
        ]
        total_input = 0
        total_output = 0
        final_text = ""
        for _ in range(self.settings.web_search_max_tool_rounds):
            completion = await client.chat.completions.create(
                model=self.settings.kimi_web_search_model,
                messages=messages,
                tools=tools,
                max_completion_tokens=32768,
            )
            usage = getattr(completion, "usage", None)
            total_input += int(
                getattr(usage, "prompt_tokens", 0) or 0
            )
            total_output += int(
                getattr(usage, "completion_tokens", 0) or 0
            )
            choice = completion.choices[0]
            message = choice.message
            if choice.finish_reason != "tool_calls":
                final_text = str(message.content or "")
                break
            messages.append(message)
            for tool_call in message.tool_calls or []:
                if tool_call.function.name != "$web_search":
                    raise RuntimeError(
                        "Kimi returned an unexpected built-in tool "
                        f"{tool_call.function.name!r}"
                    )
                arguments = json.loads(tool_call.function.arguments or "{}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": "$web_search",
                        "content": json.dumps(arguments),
                    }
                )
        else:
            raise RuntimeError(
                "Kimi web search exceeded the configured tool-round limit"
            )

        urls = list(dict.fromkeys(_URL_RE.findall(final_text)))
        results = [
            WebResult(
                title=f"Kimi source {index + 1}",
                url=url.rstrip(".,;"),
                snippet=final_text,
                score=_rank_score(index, max(len(urls), 1)),
            )
            for index, url in enumerate(urls[:top_k])
        ]
        if not results and final_text:
            results = [
                WebResult(
                    title="Kimi web-search response",
                    url="",
                    snippet=final_text,
                    score=1.0,
                )
            ]
        return WebSearchResponse(
            query=query,
            requested_provider="kimi",
            actual_provider="kimi",
            results=results,
            input_tokens=total_input,
            output_tokens=total_output,
        )

    @staticmethod
    def _stub_results(query: str, top_k: int) -> list[WebResult]:
        return [
            WebResult(
                title=f"[STUB RESULT {index + 1}] {query[:60]}",
                url=f"https://example.invalid/stub/{index + 1}",
                snippet=(
                    "Synthetic result for offline tests. This is not a fact "
                    "and must not be used as evidence."
                ),
                score=_rank_score(index, top_k),
            )
            for index in range(top_k)
        ]


def _rank_score(index: int, count: int) -> float:
    return round(max(0.0, 1.0 - index / max(count, 1)), 3)


def _url_citations(value: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("type") == "url_citation":
                citation = item.get("url_citation") or item
                url = str(citation.get("url") or "")
                if url and url not in seen:
                    seen.add(url)
                    found.append(
                        {
                            "url": url,
                            "title": str(citation.get("title") or ""),
                        }
                    )
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found


def _response_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (
        int(
            getattr(usage, "input_tokens", None)
            or getattr(usage, "prompt_tokens", 0)
            or 0
        ),
        int(
            getattr(usage, "output_tokens", None)
            or getattr(usage, "completion_tokens", 0)
            or 0
        ),
    )


_default_service: WebSearchService | None = None


def get_web_search_service() -> WebSearchService:
    global _default_service
    if _default_service is None:
        _default_service = WebSearchService()
    return _default_service


async def search_web(
    query: str,
    top_k: int = 8,
    provider: SearchProvider = "auto",
) -> list[dict[str, Any]]:
    """MCP-compatible entry point returning JSON-safe candidate records."""

    response = await get_web_search_service().search(
        query,
        provider=provider,
        top_k=top_k,
    )
    return [asdict(item) for item in response.results]
