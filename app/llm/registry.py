"""Resolve a model name to the right gateway instance.

Architectural default is Claude (Anthropic) per the Eurskem proposal —
flagship workflow YAMLs commit to claude-* models. The build currently
runs OpenAI live; Anthropic is a documented stub.

The fallback layer decouples *intent* (what the YAML asks for) from
*runtime* (what's actually available). When the intended provider is
stubbed, we resolve to the closest live equivalent via a documented map.

Providers in scope:
  - Anthropic                                  — claude-*
  - OpenAI                                     — gpt-*
  - Private Moonshot-compatible endpoint       — local-kimi-*
  - Private Z.ai-compatible endpoint           — local-glm-*
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable

import structlog

from app.config import settings
from app.llm.anthropic_gw import AnthropicGateway
from app.llm.base import LLMGateway
from app.llm.catalog import LOCAL_MODEL_NAMES, is_local_model
from app.llm.local_openai_gw import (
    GLM5LocalGateway,
    KimiK3LocalGateway,
    LocalModelProfile,
    LocalOpenAICompatibleGateway,
)
from app.llm.openai_gw import OpenAIGateway
from app.llm.errors import StructuredOutputError
from app.observability import metrics

log = structlog.get_logger(__name__)

# Architectural routing — model prefix to gateway class.
_PREFIX_ROUTES: list[tuple[str, type[LLMGateway]]] = [
    ("local-kimi-", KimiK3LocalGateway),
    ("local-glm-",  GLM5LocalGateway),
    ("claude-", AnthropicGateway),
    ("gpt-",    OpenAIGateway),
]

# Runtime fallback — when an intended model resolves to a stubbed provider,
# rewrite to the closest live equivalent.
_FALLBACK_MODEL: dict[str, str] = {
    "claude-opus-5":     "gpt-5.6-sol",
    "claude-sonnet-4-5": "gpt-5",
    "claude-haiku-4-5":  "gpt-5-mini",
    "claude-opus-4-7":   "gpt-5",
    "claude-opus-4-8":   "gpt-5",
}

# Gateway classes that are stubbed (not live).
_STUB_GATEWAYS: set[type[LLMGateway]] = set()

_INSTANCES: dict[type[LLMGateway], LLMGateway] = {}


@dataclass(frozen=True)
class RetryPolicy:
    """One explicit retry policy shared by all LLM providers.

    ``max_attempts`` includes the first request. After transient failures
    exhaust the primary model, the registry starts a fresh retry sequence on
    the mapped fallback. Validation/authentication errors fail immediately.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")


def _status_code(exc: BaseException) -> int | None:
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_retryable_error(exc: BaseException) -> bool:
    """Return whether another generation may succeed."""

    if isinstance(exc, StructuredOutputError):
        return True

    if isinstance(
        exc,
        (asyncio.TimeoutError, TimeoutError, ConnectionError),
    ):
        return True

    status = _status_code(exc)

    if status is not None:
        return status in {408, 409, 429} or 500 <= status <= 599

    return type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
        "ServiceUnavailableError",
    }

def _retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            from datetime import datetime, timezone

            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None


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
        return AnthropicGateway(api_key=settings.anthropic_api_key)
    if gw_cls is KimiK3LocalGateway:
        return KimiK3LocalGateway(_kimi_profile())
    if gw_cls is GLM5LocalGateway:
        return GLM5LocalGateway(_glm_profile())
    raise ValueError(f"Don't know how to construct {gw_cls.__name__}")


def _kimi_profile() -> LocalModelProfile:
    return LocalModelProfile(
        alias="local-kimi-k3",
        provider="moonshot-local",
        enabled=settings.local_kimi_enabled,
        base_url=settings.local_kimi_base_url,
        api_key=settings.local_kimi_api_key,
        served_model=settings.local_kimi_served_model,
        reasoning_effort=settings.local_kimi_reasoning_effort,
        timeout_seconds=settings.local_llm_timeout_seconds,
        verify_served_model=settings.local_llm_verify_served_model,
    )


def _glm_profile() -> LocalModelProfile:
    return LocalModelProfile(
        alias="local-glm-5",
        provider="zai-local",
        enabled=settings.local_glm_enabled,
        base_url=settings.local_glm_base_url,
        api_key=settings.local_glm_api_key,
        served_model=settings.local_glm_served_model,
        reasoning_effort=settings.local_glm_reasoning_effort,
        enable_thinking=settings.local_glm_enable_thinking,
        timeout_seconds=settings.local_llm_timeout_seconds,
        verify_served_model=settings.local_llm_verify_served_model,
    )


def local_model_enabled(model: str) -> bool:
    if model == "local-kimi-k3":
        return settings.local_kimi_enabled
    if model == "local-glm-5":
        return settings.local_glm_enabled
    return False


def configured_local_model_probes() -> dict[str, Callable[[], Awaitable[bool]]]:
    """Return health probes for enabled local providers without exposing URLs."""

    probes: dict[str, Callable[[], Awaitable[bool]]] = {}
    for model in LOCAL_MODEL_NAMES:
        if not local_model_enabled(model):
            continue
        gateway, _ = get_gateway(model)
        if isinstance(gateway, LocalOpenAICompatibleGateway):
            probes[f"llm:{model}"] = gateway.probe
    return probes


async def probe_local_model(model: str) -> dict[str, Any]:
    if not is_local_model(model):
        raise ValueError(f"{model!r} is not a local model")
    gateway, _ = get_gateway(model)
    if not isinstance(gateway, LocalOpenAICompatibleGateway):
        raise ValueError(f"{model!r} did not resolve to a local gateway")
    return await gateway.probe_details()


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

    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        # Context injected per-run via with_context(). None = no cost tracking.
        self._run_id:     str | None = None
        self._session_id: str | None = None
        self._node_id:    str | None = None
        self._ledger = None  # CostLedger | None
        self._retry_policy = retry_policy or RetryPolicy(
            max_attempts=settings.llm_retry_attempts,
            base_delay_seconds=settings.llm_retry_base_delay_seconds,
            max_delay_seconds=settings.llm_retry_max_delay_seconds,
            jitter_ratio=settings.llm_retry_jitter_ratio,
        )
        self._sleep = sleep
        self._random_value = random_value

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

    def _models_for_call(self, intended: str) -> list[str]:
        """Return primary then fallback, without duplicate static fallback."""

        primary = resolve_model(intended)
        candidates = [primary]
        fallback = _FALLBACK_MODEL.get(intended)
        if fallback and fallback != primary:
            candidates.append(fallback)
        return candidates

    def _delay_for(self, attempt: int, exc: BaseException) -> float:
        policy = self._retry_policy
        server_delay = _retry_after_seconds(exc)
        if server_delay is not None:
            return min(server_delay, policy.max_delay_seconds)

        raw = min(
            policy.max_delay_seconds,
            policy.base_delay_seconds * (2 ** max(0, attempt - 1)),
        )
        if raw == 0 or policy.jitter_ratio == 0:
            return raw
        # Symmetric jitter prevents synchronized parallel drafters from
        # retrying at exactly the same instant.
        factor = 1 + policy.jitter_ratio * (
            (2 * self._random_value()) - 1
        )
        return max(0.0, raw * factor)

    async def _call_resilient(
        self,
        method_name: str,
        *,
        intended: str,
        kwargs: dict[str, Any],
    ) -> tuple[Any, str]:
        candidates = self._models_for_call(intended)
        last_error: BaseException | None = None

        for model_index, candidate in enumerate(candidates):
            gateway, resolved = get_gateway(candidate)
            if model_index:
                metrics.LLM_FAILOVERS.labels(
                    from_model=candidates[0],
                    to_model=resolved,
                ).inc()
                log.warning(
                    "llm.failover",
                    intended=intended,
                    from_model=candidates[0],
                    to_model=resolved,
                    run_id=self._run_id,
                    node_id=self._node_id,
                )

            for attempt in range(1, self._retry_policy.max_attempts + 1):
                try:
                    method = getattr(gateway, method_name)
                    response = await method(model=resolved, **kwargs)
                    return response, resolved
                except Exception as exc:
                    last_error = exc
                    retryable = _is_retryable_error(exc)
                    metrics.LLM_CALLS.labels(
                        model=resolved,
                        status="error",
                    ).inc()
                    exhausted = attempt >= self._retry_policy.max_attempts

                    if not retryable:
                        log.error(
                            "llm.non_retryable_error",
                            intended=intended,
                            resolved=resolved,
                            status_code=_status_code(exc),
                            error_type=type(exc).__name__,
                            run_id=self._run_id,
                            node_id=self._node_id,
                        )
                        raise

                    if not exhausted:
                        delay = self._delay_for(attempt, exc)
                        metrics.LLM_RETRIES.labels(
                            model=resolved,
                            reason=type(exc).__name__,
                        ).inc()
                        log.warning(
                            "llm.retry",
                            intended=intended,
                            resolved=resolved,
                            attempt=attempt,
                            next_attempt=attempt + 1,
                            delay_seconds=round(delay, 3),
                            status_code=_status_code(exc),
                            error_type=type(exc).__name__,
                            run_id=self._run_id,
                            node_id=self._node_id,
                        )
                        await self._sleep(delay)
                        continue

                    log.warning(
                        "llm.provider_exhausted",
                        intended=intended,
                        resolved=resolved,
                        attempts=attempt,
                        has_fallback=model_index + 1 < len(candidates),
                        error_type=type(exc).__name__,
                        run_id=self._run_id,
                        node_id=self._node_id,
                    )
                    break

        assert last_error is not None
        raise last_error

    async def complete(self, *, model: str, **kwargs):
        resp, resolved = await self._call_resilient(
            "complete",
            intended=model,
            kwargs=kwargs,
        )
        _record_usage(model, resolved, resp)
        self._record_cost(model, resolved, resp)
        return resp

    async def complete_structured(self, *, model: str, **kwargs):
        resp, resolved = await self._call_resilient(
            "complete_structured",
            intended=model,
            kwargs=kwargs,
        )
        # Concrete gateways return StructuredResult so cost survives native
        # structured output. The registry preserves the public bare-model API.
        _record_usage(model, resolved, resp)
        self._record_cost(model, resolved, resp)
        return resp.parsed

    async def chat_with_tools(self, *, model: str, **kwargs):
        resp, resolved = await self._call_resilient(
            "chat_with_tools",
            intended=model,
            kwargs=kwargs,
        )
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
