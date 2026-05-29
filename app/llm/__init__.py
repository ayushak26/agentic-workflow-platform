"""LLM gateway package. Public API:
  - LLMGateway, LLMResponse: contract + response shape (in .base)
  - get_llm_gateway(): module-level singleton routing through the registry
"""
from .base import LLMGateway, LLMResponse
from .registry import get_llm_gateway, RegistryLLMGateway, get_gateway, resolve_model

__all__ = [
    "LLMGateway", "LLMResponse",
    "get_llm_gateway", "RegistryLLMGateway",
    "get_gateway", "resolve_model",
]