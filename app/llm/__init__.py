"""LLM gateway package. Public API:
  - LLMGateway, LLMResponse: contract + response shape (in .base)
  - get_llm_gateway(): module-level singleton used by main.py's lifespan.
    Returns RegistryLLMGateway, which dispatches directly to OpenRouter and the enterprise
    provider APIs (OpenAI, Anthropic) — see docs/architecture/LLM_MIGRATION.md for why the
    OmniRoute-proxy design (app/llm/gateway.py, client.py, context.py — removed) was reverted:
    the operational cost of running and patching a forked Node.js gateway outweighed what it
    added over calling providers directly, given OpenRouter's own routing/fallback/ZDR
    controls and Eurskem's pre-existing Python-native entity tokenizer already covered most of
    its value.
"""
from .base import LLMGateway, LLMResponse
from .registry import RegistryLLMGateway, get_gateway, get_llm_gateway, resolve_model

__all__ = [
    "LLMGateway", "LLMResponse",
    "get_llm_gateway",
    "RegistryLLMGateway",
    "get_gateway", "resolve_model",
]
