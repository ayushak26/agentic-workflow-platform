"""Abstract LLM gateway contract.

Every provider implementation subclasses LLMGateway. Nodes and retrieval
modules only ever depend on this base class, never on a concrete provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Type, TypeVar, Any

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


class LLMResponse(BaseModel):
    """Plain text response from an LLM call."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None


class ToolCall(BaseModel):
    """One tool invocation the model wants to make."""
    id: str
    name: str
    arguments: dict[str, Any]


class LLMToolUseResponse(BaseModel):
    """Response from a multi-turn chat-with-tools call.

    The model either emits final text (tool_calls is empty) or requests
    one or more tool calls (text may be None or a brief plan).
    """
    text: str | None
    tool_calls: list[ToolCall] = []
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None

class LLMGateway(ABC):
    """Abstract base for all LLM providers.

    Two methods:
      - complete():            free-text out
      - complete_structured(): Pydantic model out (provider's structured-
                               output mode under the hood)
    """

    @abstractmethod
    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Plain text completion."""
        ...

    @abstractmethod
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
        """Structured completion. Returns an instance of response_model.

        Implementations must use the provider's native structured-output
        mechanism (tool-use for Anthropic, response_format for OpenAI,
        etc.) — never parse free-text JSON.
        """
        ...
    
    @abstractmethod
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
        """Multi-turn chat with tool calling. Provider-neutral.

        Message format (neutral — implementations translate as needed):
          {"role": "user", "content": str}
          {"role": "assistant", "content": str | None,
                                "tool_calls": [{"id", "name", "arguments"}]}
          {"role": "tool", "tool_call_id": str, "content": str}

        Tool format (neutral):
          {"name": str, "description": str, "input_schema": JSONSchema-dict}

        Returns LLMToolUseResponse with either text (final answer) or
        tool_calls (model wants more info).
        """
        ...
