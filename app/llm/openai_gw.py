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

from typing import Type, TypeVar

import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.llm.base import LLMGateway, LLMResponse, LLMToolUseResponse, ToolCall

T = TypeVar("T", bound=BaseModel)

log = structlog.get_logger(__name__)


# GPT-5 family quirks ---------------------------------------------------

_GPT5_FAMILY_PREFIXES = ("gpt-5",)

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


# ---------------------------------------------------------------------


class OpenAIGateway(LLMGateway):
    """OpenAI-backed gateway. One AsyncOpenAI client per instance."""

    def __init__(self, api_key: str):
        self._client = AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        call_kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": _completion_tokens_for(model, max_tokens),
        }
        if _supports_custom_temperature(model):
            call_kwargs["temperature"] = temperature

        resp = await self._client.chat.completions.create(**call_kwargs)
        choice = resp.choices[0]
        return LLMResponse(
            text=choice.message.content or "",
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            stop_reason=choice.finish_reason,
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
    ) -> T:
        call_kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": response_model,
            "max_completion_tokens": _completion_tokens_for(model, max_tokens),
        }
        if _supports_custom_temperature(model):
            call_kwargs["temperature"] = temperature

        resp = await self._client.beta.chat.completions.parse(**call_kwargs)
        parsed = resp.choices[0].message.parsed
        if parsed is None:
            refusal = resp.choices[0].message.refusal
            raise RuntimeError(
                f"OpenAIGateway: structured parse failed for "
                f"{response_model.__name__}. refusal={refusal!r}"
            )
        return parsed
    
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
        import json  # local import keeps module-level imports lean

        # Translate neutral messages → OpenAI format
        openai_messages: list[dict] = [{"role": "system", "content": system}]
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

        response = await self._client.chat.completions.create(
            model=model,
            messages=openai_messages,
            tools=openai_tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

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
        )