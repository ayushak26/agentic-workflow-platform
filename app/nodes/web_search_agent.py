"""Typed workflow node for live public-web candidate discovery."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class WebSearchAgentInput(BaseModel):
    pass


class WebSearchAgentConfig(BaseModel):
    query: str
    provider: Literal["auto", "tavily", "openai", "kimi"] = Field(
        default="auto",
        description="Live web-search provider.",
        json_schema_extra={
            "x-enum-labels": {
                "auto": "Auto",
                "tavily": "Tavily",
                "openai": "OpenAI web search",
                "kimi": "Kimi K3 web search",
            }
        },
    )
    top_k: int = Field(default=8, ge=1, le=20)
    fallback_to_openai: bool = True


class WebSearchHit(BaseModel):
    title: str
    url: str
    snippet: str
    score: float
    status: Literal["candidate_only"] = "candidate_only"


class WebSearchAgentOutput(BaseModel):
    query: str
    requested_provider: str
    actual_provider: str
    fallback_reason: str | None = None
    results: list[WebSearchHit] = Field(default_factory=list)
    result_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@NodeRegistry.register
class WebSearchAgent(NodeType):
    type_name = "WebSearchAgent"
    description = (
        "Search the live public web with Auto, Tavily, OpenAI, or Kimi K3. "
        "Results are candidates, not verified evidence."
    )
    input_schema = WebSearchAgentInput
    config_schema = WebSearchAgentConfig
    output_schema = WebSearchAgentOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"web_search"}

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        _ = state
        cfg = WebSearchAgentConfig(**resolved_config)
        service = self.services.get("web_search")
        if service is None:
            raise RuntimeError("WebSearchAgent requires web_search service")
        response = await service.search(
            cfg.query,
            provider=cfg.provider,
            top_k=cfg.top_k,
            fallback_to_openai=cfg.fallback_to_openai,
        )
        results = [
            asdict(item) if not isinstance(item, dict) else item
            for item in response.results
        ]
        return {
            "query": response.query,
            "requested_provider": response.requested_provider,
            "actual_provider": response.actual_provider,
            "fallback_reason": response.fallback_reason,
            "results": results,
            "result_count": len(results),
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
