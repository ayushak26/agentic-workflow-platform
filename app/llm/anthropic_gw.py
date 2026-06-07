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
"""
from __future__ import annotations

import json
from typing import Type, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.llm.base import LLMGateway, LLMResponse, LLMToolUseResponse, ToolCall
from app.llm.openai_gw import StructuredResult   # reuse the carrier so cost recording works

T = TypeVar("T", bound=BaseModel)

# Models that reject the `temperature` parameter (newer reasoning models).
_NO_TEMPERATURE = {"claude-opus-4-8"}


def _supports_temperature(model: str) -> bool:
    """Return True if the model accepts a non-default temperature."""
    return model not in _NO_TEMPERATURE


class AnthropicGateway(LLMGateway):
    """Live Anthropic Claude gateway. One AsyncAnthropic client per instance."""

    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key, timeout=600.0)

    async def _create(self, **kwargs):
        """Issue a request via streaming and return the assembled Message.

        Streaming avoids the SDK's non-streaming long-request guard, which
        rejects large-max_tokens calls before they are sent. The final Message
        has the same shape as messages.create(...) would return.
        """
        async with self._client.messages.stream(**kwargs) as stream:
            return await stream.get_final_message()

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        kwargs = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        if _supports_temperature(model):
            kwargs["temperature"] = temperature

        resp = await self._create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResponse(
            text=text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=resp.stop_reason,
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
    ) -> StructuredResult:
        # Anthropic has no .parse() shortcut. Force structured output by
        # exposing a single tool whose input_schema is the Pydantic schema,
        # and requiring the model to call it. The tool input IS our JSON.
        schema = response_model.model_json_schema()
        tool_name = "emit_" + response_model.__name__.lower()

        kwargs = {
            "model": model,
            "system": system,
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

        resp = await self._create(**kwargs)
        tool_block = next(
            (b for b in resp.content if b.type == "tool_use" and b.name == tool_name),
            None,
        )
        if tool_block is None:
            raise RuntimeError(
                f"AnthropicGateway: structured call did not return tool_use for "
                f"{response_model.__name__}. stop_reason={resp.stop_reason!r}"
            )
        parsed = response_model.model_validate(tool_block.input)
        return StructuredResult(
            parsed=parsed,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=resp.model,
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
        # Translate neutral messages -> Anthropic content-block format
        anthropic_messages: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                anthropic_messages.append({"role": "user", "content": m["content"]})
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

        kwargs = {
            "model": model,
            "system": system,
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
        )