"""Anthropic Claude gateway — stub.

Default provider per the locked architecture; not currently implemented
live. The flagship workflow YAML targets Claude (claude-sonnet-4-5) and
will run against this gateway once an API key is provisioned.

To make this live: install `anthropic`, restore the AsyncAnthropic-based
implementation (tool-use for complete_structured), and add
ANTHROPIC_API_KEY to .env. No node code changes — the gateway swap is
behind the LLMGateway abstraction.
"""
from __future__ import annotations

from typing import Type, TypeVar
from pydantic import BaseModel

from app.llm.base import LLMGateway, LLMResponse, LLMToolUseResponse, ToolCall

T = TypeVar("T", bound=BaseModel)


class AnthropicGateway(LLMGateway):
    async def complete(self, **kwargs) -> LLMResponse:
        raise NotImplementedError(
            "AnthropicGateway is a documented stub. Provision "
            "ANTHROPIC_API_KEY and restore the AsyncAnthropic "
            "implementation to make live."
        )

    async def complete_structured(self, **kwargs) -> T:
        raise NotImplementedError(
            "AnthropicGateway stub — see anthropic_gw.py docstring."
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
        # Translate neutral messages → Anthropic content-block format
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

        response = await self._client.messages.create(
            model=model,
            system=system,
            messages=anthropic_messages,
            tools=anthropic_tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

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