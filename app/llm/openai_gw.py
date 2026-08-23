"""OpenAI implementation of LLMGateway.

Live provider for this build. Anthropic is the architectural default
(per the Eurskem proposal) but is currently a documented stub; OpenAI
serves all routed requests via the fallback map in registry.py.

GPT-5 family quirks absorbed in this file (so callers don't have to know):
  - max_tokens          -> max_completion_tokens
  - temperature != 1    -> dropped (GPT-5 locks temperature to default)
  - reasoning tokens    -> share max_completion_tokens budget; enforce floor

Structured output uses the SDK's .parse() shortcut with a Pydantic model
as response_format -- never free-text JSON parsing.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Type, TypeVar
import hashlib
import json

import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.llm.base import LLMGateway, LLMResponse, LLMToolUseResponse, ToolCall
from app.config import settings

T = TypeVar("T", bound=BaseModel)

log = structlog.get_logger(__name__)
from dataclasses import dataclass
from typing import Any

@dataclass
class StructuredResult:
    """Carries a parsed Pydantic model plus token usage, so the registry
    wrapper can record cost. The wrapper unwraps `.parsed` before returning
    to the caller, who still receives a bare model."""
    parsed: Any
    input_tokens: int
    output_tokens: int
    model: str
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


# GPT-5 family quirks ---------------------------------------------------

_GPT5_FAMILY_PREFIXES = ("gpt-5",)
_GPT56_FAMILY_PREFIXES = ("gpt-5.6",)

# Reasoning tokens share max_completion_tokens. A budget of 800 on a
# small chunk leaves 0 visible output once reasoning is done. Floor
# protects callers who don't know about reasoning overhead.
_GPT5_MIN_COMPLETION_TOKENS = 4096


def _supports_custom_temperature(model: str) -> bool:
    """Return True if the model accepts a non-default temperature."""
    return not model.startswith(_GPT5_FAMILY_PREFIXES)


def _completion_tokens_for(model: str, requested: int) -> int:
    """Apply provider-specific minimums to max_completion_tokens.

    GPT-5 reasoning models silently truncate to empty output if the
    budget is too tight. Older models pass through unchanged.
    """
    if model.startswith(_GPT5_FAMILY_PREFIXES):
        return max(requested, _GPT5_MIN_COMPLETION_TOKENS)
    return requested


def _cached_tokens_from_usage(usage: Any) -> int:
    """Read OpenAI's automatic-prompt-cache hit count off a usage payload.

    OpenAI caches repeated prefixes (>=1024 tokens) automatically -- no
    cache_control needed. The hit count surfaces at
    usage.prompt_tokens_details.cached_tokens; older SDK/model responses may
    omit prompt_tokens_details entirely, so every step is optional.
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    return getattr(details, "cached_tokens", 0) or 0


def _prompt_cache_key(model: str, cacheable_prefix: str) -> str:
    """Stable prompt_cache_key so requests sharing a static prefix (same
    model + system prompt) route to the same cache-holding backend --
    OpenAI's own recommendation for boosting cache hit rates. Derived from
    content rather than session/run id, since the concrete gateways don't
    receive that context (it lives on RegistryLLMGateway) -- this still
    gives every call site a consistent key without changing the
    LLMGateway interface.

    OpenAI advises ~15 requests/minute per key; if one system prompt sees
    enough concurrent traffic to exceed that, shard by mixing a session id
    into `cacheable_prefix` at the call site.
    """
    digest = hashlib.sha256(f"{model}:{cacheable_prefix}".encode("utf-8")).hexdigest()
    return f"awp-{digest[:32]}"


def _prompt_cache_retention_kwargs(model: str) -> dict:
    """Extended (24h) cache retention for pre-GPT-5.6 models, at the same
    cost as the default in-memory (5-10 min) window per OpenAI's prompt
    caching guide -- a free win for workflows with gaps between calls.
    GPT-5.6+ deprecates this field in favour of prompt_cache_options.ttl,
    which currently only supports its 30m default, so there's nothing to
    set there beyond the explicit breakpoint added in _system_content().
    """
    if model.startswith(_GPT56_FAMILY_PREFIXES):
        return {}
    return {"prompt_cache_retention": "24h"}


def _system_content(text: str, model: str | None) -> str | list[dict]:
    """Render system-prompt text as a bare string, or (gpt-5.6+ only) as a
    text content-part carrying an explicit prompt_cache_breakpoint.

    gpt-5.6+ already gets one implicit breakpoint automatically; adding an
    explicit one right after the system prompt guarantees that stable
    prefix is cached the same way AnthropicGateway marks its system block,
    independent of whatever the implicit breakpoint ends up covering.
    """
    if model is not None and model.startswith(_GPT56_FAMILY_PREFIXES):
        return [{
            "type": "text",
            "text": text,
            "prompt_cache_breakpoint": {"mode": "explicit"},
        }]
    return text


def _system_messages(system: str, *, model: str | None = None) -> list[dict]:
    """Build the leading system message, omitting it when empty.

    Some OpenAI-compatible endpoints (e.g. Moonshot's hosted Kimi K3) reject
    a request whose system message has empty content with a 400 error, so an
    unset system prompt must drop the message rather than send "".
    """
    if not system:
        return []
    return [{"role": "system", "content": _system_content(system, model)}]


def _chat_tool_reasoning_effort(model: str) -> str | None:
    """Return the safe Chat Completions effort for function-tool calls.

    GPT-5.6 function tools on Chat Completions require effective reasoning
    effort ``none``. Reasoning plus tools belongs on the Responses API; this
    gateway keeps its existing Chat Completions contract.
    """
    if model.startswith(_GPT56_FAMILY_PREFIXES):
        return "none"
    return None


# ---------------------------------------------------------------------


class OpenAIGateway(LLMGateway):
    """OpenAI-backed gateway. One AsyncOpenAI client per instance."""

    def __init__(self, api_key: str):
        """Initialize the OpenAIGateway.

        Args:
            api_key (str): API key.
        """
        self._client = AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            timeout=settings.llm_request_timeout_seconds,
        )

    async def probe_model_access(self, model: str) -> str:
        """Verify project access through model metadata, with no generation."""

        result = await self._client.models.retrieve(
            model,
            timeout=settings.llm_model_access_probe_timeout_seconds,
        )
        return result.id

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Complete the result.

        Args:
            model (str): Model name.
            system (str): The system.
            user (str): Authenticated current user.
            temperature (float): The temperature (optional, default 0.0).
            max_tokens (int): The max tokens (optional, default 1024).
            reasoning_effort (str | None): The reasoning effort (optional, default None).
            on_token (Callable[[str], Awaitable[None]] | None): The on token (optional, default None).

        Returns:
            LLMResponse: The result.
        """
        call_kwargs: dict = {
            "model": model,
            "messages": [
                *_system_messages(system, model=model),
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": _completion_tokens_for(model, max_tokens),
            "prompt_cache_key": _prompt_cache_key(model, system),
            **_prompt_cache_retention_kwargs(model),
        }
        if reasoning_effort is not None and model.startswith(_GPT5_FAMILY_PREFIXES):
            call_kwargs["reasoning_effort"] = reasoning_effort
        if _supports_custom_temperature(model):
            call_kwargs["temperature"] = temperature

        if on_token is None:
            resp = await self._client.chat.completions.create(**call_kwargs)
            choice = resp.choices[0]
            return LLMResponse(
                text=choice.message.content or "",
                model=resp.model,
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
                stop_reason=choice.finish_reason,
                cache_read_input_tokens=_cached_tokens_from_usage(resp.usage),
            )

        stream = await self._client.chat.completions.create(
            **call_kwargs,
            stream=True,
            stream_options={"include_usage": True},
        )
        parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        stop_reason = None
        response_model = model
        async for chunk in stream:
            response_model = chunk.model or response_model
            if chunk.usage is not None:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens
                cached_tokens = _cached_tokens_from_usage(chunk.usage)
            for choice in chunk.choices:
                if choice.finish_reason is not None:
                    stop_reason = choice.finish_reason
                token = choice.delta.content or ""
                if token:
                    parts.append(token)
                    await on_token(token)
        return LLMResponse(
            text="".join(parts),
            model=response_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            cache_read_input_tokens=cached_tokens,
        )

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> T:
        # Non-strict JSON mode: allows free-form object fields that OpenAI's
        # strict schema mode (.parse) rejects. We validate with Pydantic
        # ourselves; the caller's retry loop re-prompts on validation failure.
        """Complete the structured.

        Args:
            model (str): Model name.
            system (str): The system.
            user (str): Authenticated current user.
            response_model (Type[T]): The response model.
            temperature (float): The temperature (optional, default 0.0).
            max_tokens (int): The max tokens (optional, default 4096).
            reasoning_effort (str | None): The reasoning effort (optional, default None).

        Returns:
            T: The structured.
        """
        schema = response_model.model_json_schema()
        system_with_schema = (
            f"{system}\n\n"
            "Respond with a single JSON object that conforms to this schema. "
            "Return every field using its native JSON type (objects as JSON "
            "objects, lists as JSON arrays). Output only the JSON, no prose.\n"
            f"SCHEMA:\n{json.dumps(schema)}"
        )
        call_kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": _system_content(system_with_schema, model)},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": _completion_tokens_for(model, max_tokens),
            "prompt_cache_key": _prompt_cache_key(model, system_with_schema),
            **_prompt_cache_retention_kwargs(model),
        }
        if reasoning_effort is not None and model.startswith(_GPT5_FAMILY_PREFIXES):
            call_kwargs["reasoning_effort"] = reasoning_effort
        if _supports_custom_temperature(model):
            call_kwargs["temperature"] = temperature

        resp = await self._client.chat.completions.create(**call_kwargs)
        content = resp.choices[0].message.content
        if not content:
            refusal = getattr(resp.choices[0].message, "refusal", None)
            raise RuntimeError(
                f"OpenAIGateway: empty structured response for "
                f"{response_model.__name__}. refusal={refusal!r}"
            )
        # Validate against the Pydantic model (raises ValidationError on mismatch,
        # which the caller's retry loop catches and re-prompts on).
        instance = response_model.model_validate_json(content)
        usage = resp.usage
        return StructuredResult(
            parsed=instance,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=resp.model,
            cache_read_input_tokens=_cached_tokens_from_usage(usage),
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
        """Compute the chat with tools.

        Args:
            model (str): Model name.
            system (str): The system.
            messages (list[dict]): The messages.
            tools (list[dict]): The tools.
            temperature (float): The temperature (optional, default 0.0).
            max_tokens (int): The max tokens (optional, default 4096).

        Returns:
            LLMToolUseResponse: The with tools.
        """
        import json  # local import keeps module-level imports lean

        # Translate neutral messages → OpenAI format
        openai_messages: list[dict] = _system_messages(system, model=model)
        for m in messages:
            role = m["role"]
            if role == "user":
                openai_messages.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content")}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in m["tool_calls"]
                    ]
                openai_messages.append(msg)
            elif role == "tool":
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": m["tool_call_id"],
                    "content": m["content"],
                })

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

        call_kwargs: dict = {
            "model": model,
            "messages": openai_messages,
            "tools": openai_tools,
            "max_completion_tokens": _completion_tokens_for(model, max_tokens),
            "prompt_cache_key": _prompt_cache_key(model, system),
            **_prompt_cache_retention_kwargs(model),
        }
        reasoning_effort = _chat_tool_reasoning_effort(model)
        if reasoning_effort is not None:
            call_kwargs["reasoning_effort"] = reasoning_effort
        if _supports_custom_temperature(model):
            call_kwargs["temperature"] = temperature

        response = await self._client.chat.completions.create(**call_kwargs)


        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        return LLMToolUseResponse(
            text=message.content,
            tool_calls=tool_calls,
            model=response.model,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            stop_reason=choice.finish_reason,
            cache_read_input_tokens=_cached_tokens_from_usage(response.usage),
        )
