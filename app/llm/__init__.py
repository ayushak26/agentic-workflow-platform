"""LLM provider gateway.

Public API:
    LLMGateway       — abstract base
    LLMResponse      — plain text response
    get_gateway(name)— resolve a model name to its gateway
"""
from app.llm.base import LLMGateway, LLMResponse
from app.llm.registry import get_gateway

__all__ = ["LLMGateway", "LLMResponse", "get_gateway"]