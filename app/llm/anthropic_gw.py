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

from app.llm.base import LLMGateway, LLMResponse

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