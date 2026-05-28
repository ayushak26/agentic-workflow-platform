"""OpenAI implementation of LLMGateway.

Live provider for this build. Anthropic is the architectural default
(per the Optimoz proposal) but is currently a documented stub; OpenAI
serves all routed requests via the fallback map in registry.py.

GPT-5 family quirks absorbed in this file (so callers don't have to know):
  - max_tokens          → max_completion_tokens
  - temperature != 1    → dropped (GPT-5 locks temperature to default)

Structured output uses the SDK's .parse() shortcut with a Pydantic model
as response_format — never free-text JSON parsing.
"""
from __future__ import annotations

from typing import Type, TypeVar

import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.llm.base import LLMGateway, LLMResponse

T = TypeVar("T", bound=BaseModel)

log = structlog.get_logger(__name__)


# GPT-5 family models lock temperature to the default (1). Older models
# still accept custom values. Centralize the policy here so callers can
# keep passing temperature=0.0 without caring which model family they hit.
_GPT5_FAMILY_PREFIXES = ("gpt-5",)


def _supports_custom_temperature(model: str) -> bool:
    """Return True if the model accepts a non-default temperature."""
    return not model.startswith(_GPT5_FAMILY_PREFIXES)


class OpenAIGateway(LLMGateway):
    """OpenAI-backed gateway. One AsyncOpenAI client per instance,
    reused across the whole app."""

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
            "max_completion_tokens": max_tokens,
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
        max_tokens: int = 1024,
    ) -> T:
        call_kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": response_model,
            "max_completion_tokens": max_tokens,
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