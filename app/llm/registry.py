"""Resolve a model name to the right gateway instance.

Architectural default is Claude (Anthropic) per the Optimoz proposal —
flagship workflow YAMLs commit to claude-* models. The build currently
runs OpenAI live; Anthropic is a documented stub.

The fallback layer below decouples *intent* (what the YAML asks for) from
*runtime* (what's actually available). When the intended provider is
stubbed, we resolve to the closest live equivalent via a documented map.

This is not just dev plumbing — it's a degradation pattern. In prod, if
the primary provider is degraded, the same mechanism would route to a
secondary provider with no workflow changes.

Two providers in scope:
  - Anthropic (stub, default per architecture) — claude-*
  - OpenAI    (live)                            — gpt-*
"""
from __future__ import annotations

import structlog

from app.config import settings
from app.llm.anthropic_gw import AnthropicGateway
from app.llm.base import LLMGateway
from app.llm.openai_gw import OpenAIGateway

log = structlog.get_logger(__name__)


# Architectural routing — model prefix to gateway class.
_PREFIX_ROUTES: list[tuple[str, type[LLMGateway]]] = [
    ("claude-", AnthropicGateway),
    ("gpt-",    OpenAIGateway),
]

# Runtime fallback — when an intended model resolves to a stubbed provider,
# rewrite to the closest live equivalent. Documented mapping, not a guess.
_FALLBACK_MODEL: dict[str, str] = {
    "claude-sonnet-4-5": "gpt-5",
    "claude-haiku-4-5":  "gpt-5-mini",
    "claude-opus-4-7":   "gpt-5",
}

# Set of gateway classes that are stubbed (not live). When the intended
# provider is in this set, we apply the fallback.
_STUB_GATEWAYS: set[type[LLMGateway]] = {AnthropicGateway}

_INSTANCES: dict[type[LLMGateway], LLMGateway] = {}


def resolve_model(intended: str) -> str:
    """Map an intended model name to the model name we'll actually call.

    Pure function — no instantiation, easy to test. Logs every fallback so
    the eval lab and audit log can see when a workflow ran on a fallback
    vs its intended provider.
    """
    gw_cls = _gateway_class_for(intended)
    if gw_cls in _STUB_GATEWAYS:
        fallback = _FALLBACK_MODEL.get(intended)
        if fallback is None:
            raise ValueError(
                f"Model {intended!r} routes to a stubbed provider "
                f"({gw_cls.__name__}) and has no fallback mapped. "
                f"Add to _FALLBACK_MODEL or provision provider credentials."
            )
        log.info("llm.fallback", intended=intended, resolved=fallback)
        return fallback
    return intended


def get_gateway(model_name: str) -> tuple[LLMGateway, str]:
    """Return (gateway, resolved_model_name).

    Callers MUST use the returned model name when calling the gateway —
    a fallback may have rewritten it. The caller can also log both names
    for traceability (intended vs actual).
    """
    resolved = resolve_model(model_name)
    gw_cls = _gateway_class_for(resolved)
    if gw_cls not in _INSTANCES:
        _INSTANCES[gw_cls] = _construct(gw_cls)
    return _INSTANCES[gw_cls], resolved


def _gateway_class_for(model_name: str) -> type[LLMGateway]:
    for prefix, gw_cls in _PREFIX_ROUTES:
        if model_name.startswith(prefix):
            return gw_cls
    raise ValueError(
        f"No gateway registered for model {model_name!r}. "
        f"Known prefixes: {[p for p, _ in _PREFIX_ROUTES]}"
    )


def _construct(gw_cls: type[LLMGateway]) -> LLMGateway:
    if gw_cls is OpenAIGateway:
        return OpenAIGateway(api_key=settings.openai_api_key)
    if gw_cls is AnthropicGateway:
        return AnthropicGateway()
    raise ValueError(f"Don't know how to construct {gw_cls.__name__}")

class RegistryLLMGateway(LLMGateway):
    """LLMGateway adapter that dispatches every call through the registry.

    Nodes consume a single LLMGateway instance via DI. This adapter is that
    instance — it doesn't bind to one provider. On every call it asks the
    registry which concrete gateway handles the requested model, applies
    any documented fallback (e.g., claude-sonnet-4-5 → gpt-5 while the
    Anthropic provider is stubbed), and delegates.

    Interview line: 'per-node YAML picks the model, the adapter routes,
    the registry's fallback table is the degradation pattern we'd use in
    prod when a primary provider is degraded.'
    """

    async def complete(self, *, model: str, **kwargs):
        gateway, resolved = get_gateway(model)
        return await gateway.complete(model=resolved, **kwargs)

    async def complete_structured(self, *, model: str, **kwargs):
        gateway, resolved = get_gateway(model)
        return await gateway.complete_structured(model=resolved, **kwargs)
    
    async def chat_with_tools(self, *, model: str, **kwargs):
        gateway, resolved = get_gateway(model)
        return await gateway.chat_with_tools(model=resolved, **kwargs)


_default_registry_gateway: RegistryLLMGateway | None = None


def get_llm_gateway() -> RegistryLLMGateway:
    """Module-level singleton used by main.py's lifespan."""
    global _default_registry_gateway
    if _default_registry_gateway is None:
        _default_registry_gateway = RegistryLLMGateway()
    return _default_registry_gateway