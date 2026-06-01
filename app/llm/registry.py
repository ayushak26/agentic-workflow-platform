"""Resolve a model name to the right gateway instance.

Architectural default is Claude (Anthropic) per the Eurskem proposal —
flagship workflow YAMLs commit to claude-* models. The build currently
runs OpenAI live; Anthropic is a documented stub.

The fallback layer decouples *intent* (what the YAML asks for) from
*runtime* (what's actually available). When the intended provider is
stubbed, we resolve to the closest live equivalent via a documented map.

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
from app.observability import metrics

log = structlog.get_logger(__name__)

# Architectural routing — model prefix to gateway class.
_PREFIX_ROUTES: list[tuple[str, type[LLMGateway]]] = [
    ("claude-", AnthropicGateway),
    ("gpt-",    OpenAIGateway),
]

# Runtime fallback — when an intended model resolves to a stubbed provider,
# rewrite to the closest live equivalent.
_FALLBACK_MODEL: dict[str, str] = {
    "claude-sonnet-4-5": "gpt-5",
    "claude-haiku-4-5":  "gpt-5-mini",
    "claude-opus-4-7":   "gpt-5",
}

# Gateway classes that are stubbed (not live).
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

    Callers MUST use the returned model name — a fallback may have
    rewritten the intended name. Both names are available for audit logging.
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


def _record_usage(intended: str, resolved: str, resp) -> None:
    """Record token metrics. Never raises — instrumentation must not break calls."""
    try:
        metrics.LLM_CALLS.labels(model=resolved, status="success").inc()
        in_tok  = getattr(resp, "input_tokens",  0) or 0
        out_tok = getattr(resp, "output_tokens", 0) or 0
        if in_tok:
            metrics.LLM_TOKENS.labels(model=resolved, direction="prompt").inc(in_tok)
        if out_tok:
            metrics.LLM_TOKENS.labels(model=resolved, direction="completion").inc(out_tok)
    except Exception:
        pass


class RegistryLLMGateway(LLMGateway):
    """LLMGateway adapter that dispatches every call through the registry.

    Nodes consume a single LLMGateway instance via DI. This adapter doesn't
    bind to one provider — it routes per call. On every call it:
      1. Resolves the intended model name (applying fallback if needed)
      2. Delegates to the right concrete gateway
      3. Records token metrics + cost to the ledger

    Interview line: 'per-node YAML picks the model, the adapter routes,
    the registry's fallback table is the degradation pattern we'd use in
    prod when a primary provider is degraded.'
    """

    def __init__(self) -> None:
        # Context injected per-run via with_context(). None = no cost tracking.
        self._run_id:     str | None = None
        self._session_id: str | None = None
        self._node_id:    str | None = None
        self._ledger = None  # CostLedger | None

    def with_context(
        self,
        run_id: str,
        session_id: str,
        node_id: str,
        ledger=None,
    ) -> "RegistryLLMGateway":
        """Return a context-bound copy for per-node cost tracking.

        Called by the executor before handing the gateway to each node.
        Does not mutate the singleton — returns a shallow clone so parallel
        branches each get independent context.
        """
        clone = RegistryLLMGateway.__new__(RegistryLLMGateway)
        clone.__dict__.update(self.__dict__)
        clone._run_id     = run_id
        clone._session_id = session_id
        clone._node_id    = node_id
        clone._ledger     = ledger
        return clone

    def _record_cost(self, intended: str, resolved: str, resp) -> None:
        """Write a LedgerEntry if we have context + a ledger."""
        if self._ledger is None:
            return
        try:
            from app.observability.cost_ledger import CostLedger, LedgerEntry
            entry = LedgerEntry(
                run_id=self._run_id or "unknown",
                session_id=self._session_id or "unknown",
                node_id=self._node_id or "unknown",
                model=resolved,
                intended_model=intended,          # audit: what the YAML asked for
                input_tokens=getattr(resp, "input_tokens",  0) or 0,
                output_tokens=getattr(resp, "output_tokens", 0) or 0,
                cost_usd=CostLedger.calculate(
                    resolved,
                    getattr(resp, "input_tokens",  0) or 0,
                    getattr(resp, "output_tokens", 0) or 0,
                ),
            )
            self._ledger.record(entry)
        except Exception:
            pass  # cost tracking must never break the actual LLM call

    async def complete(self, *, model: str, **kwargs):
        gateway, resolved = get_gateway(model)
        resp = await gateway.complete(model=resolved, **kwargs)
        _record_usage(model, resolved, resp)
        self._record_cost(model, resolved, resp)
        return resp

    async def complete_structured(self, *, model: str, **kwargs):
        gateway, resolved = get_gateway(model)
        resp = await gateway.complete_structured(model=resolved, **kwargs)
        # resp is a StructuredResult: carries parsed model + token usage.
        _record_usage(model, resolved, resp)
        self._record_cost(model, resolved, resp)
        return resp.parsed

    async def chat_with_tools(self, *, model: str, **kwargs):
        gateway, resolved = get_gateway(model)
        resp = await gateway.chat_with_tools(model=resolved, **kwargs)
        _record_usage(model, resolved, resp)
        self._record_cost(model, resolved, resp)
        return resp


_default_registry_gateway: RegistryLLMGateway | None = None


def get_llm_gateway() -> RegistryLLMGateway:
    """Module-level singleton used by main.py's lifespan."""
    global _default_registry_gateway
    if _default_registry_gateway is None:
        _default_registry_gateway = RegistryLLMGateway()
    return _default_registry_gateway