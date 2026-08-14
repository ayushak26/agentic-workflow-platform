"""OpenRouter implementation of LLMGateway — direct calls to OpenRouter's real API.

Raw httpx rather than an SDK: OpenRouter's `usage.cost` field (real, authoritative,
per-call cost in USD — https://openrouter.ai/docs/use-cases/usage-accounting) is not part of
the OpenAI SDK's typed response schema, so going through `AsyncOpenAI` risks that field being
silently dropped during parsing. httpx gives direct access to the raw JSON body.

Model addressing: this gateway is routed to via the `"openrouter/"` prefix in
app/llm/registry.py's _PREFIX_ROUTES (e.g. "openrouter/openai/gpt-4o-mini",
"openrouter/auto" for OpenRouter's own router — see https://openrouter.ai/docs/guides/
routing/routers/auto-router). The prefix is Eurskem's own gateway-selection convention;
OpenRouter's real API never sees it — it's stripped before the request is sent.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Type, TypeVar

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm.base import LLMGateway, LLMResponse, LLMToolUseResponse, ToolCall

T = TypeVar("T", bound=BaseModel)

log = structlog.get_logger(__name__)

_PREFIX = "openrouter/"

# Some upstreams honour `response_format: {"type": "json_object"}` by returning the JSON
# inside a Markdown code fence, which is not itself valid JSON. Confirmed live against
# OpenRouter (2026-08-14): anthropic/claude-haiku-4.5, claude-sonnet-4.5 and
# claude-sonnet-4.6 all fence their output, while claude-opus-5 and claude-sonnet-5 do
# not — so this is per-model upstream behaviour, not something a request parameter can
# turn off. Without unfencing, every structured-output node fails on those models.
_JSON_FENCE_PATTERN = re.compile(
    r"\A\s*```[^\S\n]*(?:json)?[^\S\n]*\n(?P<body>.*?)\n?\s*```\s*\Z",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class StructuredResult:
    """Mirrors app/llm/openai_gw.py's StructuredResult — carries usage/cost alongside the
    parsed model so the registry wrapper can record both, then unwraps `.parsed`."""
    parsed: Any
    input_tokens: int
    output_tokens: int
    model: str
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float | None = None


def strip_openrouter_prefix(model: str) -> str:
    return model[len(_PREFIX):] if model.startswith(_PREFIX) else model


def unfence_json(content: str) -> str:
    """Return the JSON body of a Markdown-fenced payload, or `content` unchanged.

    Only whole-string fences are unwrapped. Anything else — prose around the JSON, a
    fence mid-string — is left alone for the caller's parse to reject: guessing at where
    the JSON starts inside arbitrary text risks silently parsing the wrong span, which is
    worse than a loud validation error.
    """
    match = _JSON_FENCE_PATTERN.match(content)
    return match.group("body").strip() if match else content


def _parse_structured(content: str, response_model: Type[T]) -> T:
    """Parse a structured response, retrying once on the unfenced body.

    The direct parse is tried first so the overwhelmingly common well-behaved case keeps
    its existing behaviour exactly; unfencing is a recovery path, never the default.
    """
    try:
        return response_model.model_validate_json(content)
    except ValidationError:
        unfenced = unfence_json(content)
        if unfenced == content:
            raise
        log.debug(
            "openrouter.structured_output_unfenced",
            response_model=response_model.__name__,
        )
        return response_model.model_validate_json(unfenced)


def _usage_fields(usage: dict[str, Any] | None) -> dict[str, Any]:
    usage = usage or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return {
        "input_tokens": usage.get("prompt_tokens") or 0,
        "output_tokens": usage.get("completion_tokens") or 0,
        "cache_read_input_tokens": prompt_details.get("cached_tokens") or 0,
        "cache_creation_input_tokens": prompt_details.get("cache_write_tokens") or 0,
        # "Cost in credits" per OpenRouter's docs — 1 credit = $1 USD. None (not 0.0) when
        # absent, so callers can tell "OpenRouter didn't report a cost" from "this was free".
        "cost_usd": usage.get("cost"),
    }


class OpenRouterGateway(LLMGateway):
    """OpenRouter-backed gateway. One httpx.AsyncClient per instance."""

    def __init__(self, api_key: str, *, base_url: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url or settings.openrouter_base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=settings.llm_request_timeout_seconds,
        )

    async def probe_model_access(self, model: str) -> str:
        """Cheap existence check via the catalog — OpenRouter has no per-model
        equivalent of OpenAI's GET /models/{id}."""
        from app.llm.openrouter_catalog import get_default_cache

        bare = strip_openrouter_prefix(model)
        models = await get_default_cache().get_models()
        if any(strip_openrouter_prefix(m.id) == bare for m in models):
            return model
        raise ValueError(f"OpenRouter model {bare!r} not found in the live catalog")

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("/chat/completions", json=body)
        response.raise_for_status()
        return response.json()

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": strip_openrouter_prefix(model),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort

        resp = await self._post(body)
        choice = (resp.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        fields = _usage_fields(resp.get("usage"))
        return LLMResponse(
            text=message.get("content") or "",
            model=resp.get("model") or model,
            stop_reason=choice.get("finish_reason"),
            **fields,
        )

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> StructuredResult:
        schema = response_model.model_json_schema()
        system_with_schema = (
            f"{system}\n\n"
            "Respond with a single JSON object that conforms to this schema. "
            "Return every field using its native JSON type (objects as JSON "
            "objects, lists as JSON arrays). Output only the JSON, no prose.\n"
            f"SCHEMA:\n{json.dumps(schema)}"
        )
        body: dict[str, Any] = {
            "model": strip_openrouter_prefix(model),
            "messages": [
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort

        resp = await self._post(body)
        choice = (resp.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        if not content:
            raise RuntimeError(
                f"OpenRouterGateway: empty structured response for {response_model.__name__}"
            )
        instance = _parse_structured(content, response_model)
        fields = _usage_fields(resp.get("usage"))
        return StructuredResult(
            parsed=instance,
            model=resp.get("model") or model,
            **fields,
        )

    async def chat_with_tools(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMToolUseResponse:
        openai_messages: list[dict] = [{"role": "system", "content": system}] if system else []
        for msg in messages:
            role = msg["role"]
            if role == "assistant" and msg.get("tool_calls"):
                openai_messages.append({
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in msg["tool_calls"]
                    ],
                })
            elif role == "tool":
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg.get("content") or "",
                })
            else:
                openai_messages.append({"role": role, "content": msg.get("content")})

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

        body: dict[str, Any] = {
            "model": strip_openrouter_prefix(model),
            "messages": openai_messages,
            "tools": openai_tools,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        resp = await self._post(body)
        choice = (resp.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=_safe_json_loads(tc["function"]["arguments"]),
            )
            for tc in (message.get("tool_calls") or [])
        ]
        fields = _usage_fields(resp.get("usage"))
        return LLMToolUseResponse(
            text=message.get("content"),
            tool_calls=tool_calls,
            model=resp.get("model") or model,
            stop_reason=choice.get("finish_reason"),
            **fields,
        )


def _safe_json_loads(value: str) -> dict[str, Any]:
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}
