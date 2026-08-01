"""Anthropic Claude gateway.

Default provider per the locked architecture. The flagship workflow YAML
targets Claude; this gateway runs against the Anthropic API once
ANTHROPIC_API_KEY is provisioned. The gateway swap is behind the LLMGateway
abstraction, so no node code changes when switching providers.

Temperature note: newer reasoning models (e.g. claude-opus-4-8) reject the
`temperature` parameter. We gate it the same way the OpenAI gateway does —
only send `temperature` when the model accepts it. Add models that reject it
to _NO_TEMPERATURE.

Streaming note: large non-streamed requests trip the Anthropic SDK's
long-request pre-flight guard (raised BEFORE the network call, so a higher
client `timeout` does not help). We therefore issue every request via
`messages.stream(...)` and assemble the final message with
`get_final_message()`. The returned Message is identical in shape to the
non-streamed response, so all downstream extraction (text blocks, tool_use
blocks, usage) is unchanged. This is the SDK's recommended path for long
generations.

Prompt caching: every call marks the system prompt (which, per Anthropic's
render order tools -> system -> messages, also covers any tools declared on
the same request) with an ephemeral cache_control breakpoint, and
chat_with_tools additionally marks the last content block of the outgoing
message list so the growing multi-turn history caches across turns. This is
unconditional -- prompts under the model's minimum cacheable prefix (512-4096
tokens depending on model) simply don't cache; there's no error and no
required opt-in. See settings.anthropic_prompt_cache_ttl for the TTL.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Type, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMGateway, LLMResponse, LLMToolUseResponse, ToolCall
from app.llm.errors import StructuredOutputError
from app.llm.openai_gw import StructuredResult  # shared usage/cost carrier
from app.config import settings

T = TypeVar("T", bound=BaseModel)

# Models that reject non-default sampling parameters.
_NO_TEMPERATURE = {
    "claude-opus-4-8",
    "claude-opus-5",
}


def _supports_temperature(model: str) -> bool:
    """Return True if the model accepts a non-default temperature."""
    return model not in _NO_TEMPERATURE


def _cacheable_system(system: str) -> str | list[dict]:
    """Turn a plain system string into a cache-marked content-block list.

    Anthropic's system param accepts a plain string or a list of content
    blocks; cache_control only attaches to a block, so a non-empty system
    prompt is rendered as a single text block carrying the breakpoint. An
    empty system prompt is passed through unchanged (some call sites rely on
    "" meaning "no system prompt").
    """
    if not system:
        return system
    return [{
        "type": "text",
        "text": system,
        "cache_control": {
            "type": "ephemeral",
            "ttl": settings.anthropic_prompt_cache_ttl,
        },
    }]


def _mark_last_block_cacheable(messages: list[dict]) -> None:
    """Add a cache breakpoint to the last content block of the last message.

    Mutates `messages` in place. Each request's growing history reuses the
    entire prior conversation prefix once the previous request's marked
    prefix matches byte-for-byte -- see prompt-caching placement guidance for
    multi-turn conversations. No-op on an empty message list or a message
    whose content isn't a block list (plain-string user content, which none
    of our chat_with_tools translations produce).
    """
    if not messages:
        return
    content = messages[-1].get("content")
    if not isinstance(content, list) or not content:
        return
    content[-1] = {
        **content[-1],
        "cache_control": {
            "type": "ephemeral",
            "ttl": settings.anthropic_prompt_cache_ttl,
        },
    }


def _cache_usage_fields(usage) -> dict[str, int]:
    """Extract cache_creation/cache_read counts off a Message.usage object."""
    return {
        "cache_creation_input_tokens": getattr(
            usage, "cache_creation_input_tokens", 0
        ) or 0,
        "cache_read_input_tokens": getattr(
            usage, "cache_read_input_tokens", 0
        ) or 0,
    }


class AnthropicGateway(LLMGateway):
    """Live Anthropic Claude gateway. One AsyncAnthropic client per instance."""

    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(
            api_key=api_key,
            timeout=settings.llm_request_timeout_seconds,
            max_retries=0,
        )

    async def probe_model_access(self, model: str) -> str:
        """Verify workspace access through model metadata, with no generation."""

        result = await self._client.models.retrieve(
            model_id=model,
            timeout=settings.llm_model_access_probe_timeout_seconds,
        )
        return result.id

    async def _create(
        self,
        *,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        """Issue a request via streaming and return the assembled Message.

        Streaming avoids the SDK's non-streaming long-request guard, which
        rejects large-max_tokens calls before they are sent. The final Message
        has the same shape as messages.create(...) would return.
        """
        async with self._client.messages.stream(**kwargs) as stream:
            if on_token is not None:
                async for text in stream.text_stream:
                    await on_token(text)
            return await stream.get_final_message()

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
        # Anthropic has no reasoning_effort knob wired here; accepted only
        # for interface parity with OpenAIGateway.
        _ = reasoning_effort
        kwargs = {
            "model": model,
            "system": _cacheable_system(system),
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        if _supports_temperature(model):
            kwargs["temperature"] = temperature

        resp = await self._create(on_token=on_token, **kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResponse(
            text=text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=resp.stop_reason,
            **_cache_usage_fields(resp.usage),
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
    ) -> StructuredResult:
        # Anthropic has no reasoning_effort knob wired here; accepted only
        # for interface parity with OpenAIGateway.
        _ = reasoning_effort
        # Anthropic has no .parse() shortcut. Force structured output by
        # exposing a single tool whose input_schema is the Pydantic schema,
        # and requiring the model to call it. The tool input IS our JSON.
        schema = response_model.model_json_schema()
        tool_name = "emit_" + response_model.__name__.lower()

        kwargs = {
            "model": model,
            "system": _cacheable_system(system),
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "tools": [{
                "name": tool_name,
                "description": f"Emit a well-formed {response_model.__name__}.",
                "input_schema": schema,
            }],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        if _supports_temperature(model):
            kwargs["temperature"] = temperature

        # Keep structured generations on the same streaming path as text and
        # tool-chat calls. This avoids Anthropic's long-request pre-flight
        # guard for schemas/prompts with a large max_tokens value.
        resp = await self._create(**kwargs)
        tool_block = next(
            (b for b in resp.content if b.type == "tool_use" and b.name == tool_name),
            None,
        )
        if tool_block is None:
            raise StructuredOutputError(
                "AnthropicGateway did not return the required tool call for "
                f"{response_model.__name__}. stop_reason={resp.stop_reason!r}"
            )
        try:
            parsed = response_model.model_validate(tool_block.input)
        except ValidationError as exc:
            keys = (
                sorted(tool_block.input)
                if isinstance(tool_block.input, dict)
                else []
            )
            raise StructuredOutputError(
                "AnthropicGateway returned invalid model-generated structured "
                f"output for {response_model.__name__}; "
                f"top_level_keys={keys!r}; stop_reason={resp.stop_reason!r}"
            ) from exc
        return StructuredResult(
            parsed=parsed,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=resp.model,
            **_cache_usage_fields(resp.usage),
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
        # Translate neutral messages -> Anthropic content-block format.
        # User content is always rendered as a block list (not a bare
        # string) so a cache_control breakpoint can attach to it below
        # regardless of which role ends the conversation.
        anthropic_messages: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{"type": "text", "text": m["content"]}],
                })
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["arguments"],
                    })
                anthropic_messages.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                # Anthropic puts tool results inside a user message
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": m["content"],
                    }],
                })

        anthropic_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools
        ]

        _mark_last_block_cacheable(anthropic_messages)

        kwargs = {
            "model": model,
            "system": _cacheable_system(system),
            "messages": anthropic_messages,
            "tools": anthropic_tools,
            "max_tokens": max_tokens,
        }
        if _supports_temperature(model):
            kwargs["temperature"] = temperature

        response = await self._create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=dict(block.input),
                ))

        return LLMToolUseResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            **_cache_usage_fields(response.usage),
        )
