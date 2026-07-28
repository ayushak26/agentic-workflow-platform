"""Resolve a model name to the right gateway instance.

Architectural default is Claude (Anthropic) per the Eurskem proposal and both
provider gateways are live. Availability is derived from configured
credentials; missing providers are skipped before a request is attempted.

The fallback layer decouples *intent* (what the YAML asks for) from
*runtime* (what's actually available). When the intended provider is
stubbed, we resolve to the closest live equivalent via a documented map.

Two providers in scope:
  - Anthropic (stub, default per architecture) — claude-*
  - OpenAI    (live)                            — gpt-*
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable

import structlog

from app.config import settings
from app.llm.anthropic_gw import AnthropicGateway
from app.llm.base import LLMGateway, LLMResponse
from app.llm.openai_gw import OpenAIGateway
from app.llm.errors import (
    LLMInputLimitError,
    LLMProviderUnavailableError,
    StructuredOutputError,
)
from app.llm.model_catalog import AUTO_MODEL, DEFAULT_LLM_MODELS
from app.llm.model_router import (
    ModelRouter,
    ModelRoutingError,
    ModelSelection,
)
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
    "claude-opus-5":     "gpt-5.6-sol",
    "claude-sonnet-4-5": "gpt-5",
    "claude-haiku-4-5":  "gpt-5-mini",
    "claude-opus-4-7":   "gpt-5",
    "claude-opus-4-8":   "gpt-5",
    "gpt-5.6-sol":        "claude-opus-5",
    "gpt-5":              "claude-sonnet-4-5",
    "gpt-5-mini":         "claude-haiku-4-5",
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


def _provider_is_configured(model_name: str) -> bool:
    gateway = _gateway_class_for(model_name)
    if gateway in _INSTANCES:
        return True
    if gateway is OpenAIGateway:
        return bool(settings.openai_api_key.strip())
    if gateway is AnthropicGateway:
        return bool(settings.anthropic_api_key.strip())
    return False


def _estimate_input_tokens(method_name: str, kwargs: dict[str, Any]) -> int:
    """Estimate the complete provider payload before spending any tokens."""

    if method_name in {"complete", "complete_structured"}:
        payload = "\n".join(
            (str(kwargs.get("system", "")), str(kwargs.get("user", "")))
        )
        response_model = kwargs.get("response_model")
        if response_model is not None:
            try:
                payload += json.dumps(response_model.model_json_schema())
            except Exception:
                payload += str(response_model)
    else:
        payload = json.dumps(
            {
                "system": kwargs.get("system", ""),
                "messages": kwargs.get("messages", []),
                "tools": kwargs.get("tools", []),
            },
            default=str,
            separators=(",", ":"),
        )
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(payload))
    except Exception:
        return max(1, len(payload) // 4)


def _construct(gw_cls: type[LLMGateway]) -> LLMGateway:
    if gw_cls is OpenAIGateway:
        return OpenAIGateway(api_key=settings.openai_api_key)
    if gw_cls is AnthropicGateway:
        return AnthropicGateway(api_key=settings.anthropic_api_key)
    raise ValueError(f"Don't know how to construct {gw_cls.__name__}")


def _record_usage(intended: str, resolved: str, resp) -> None:
    """Record token metrics. Never raises — instrumentation must not break calls."""
    if getattr(resp, "cache_hit", False):
        return
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
        self._semantic_cache = None
        self._event_bus = None
        self._node_type: str | None = None
        self._allowed_models = list(DEFAULT_LLM_MODELS)
        self._routing_policy: dict[str, Any] = {}
        self._selection_history: list[dict[str, Any]] = []
        self._call_sequence = 0
        self._model_router = ModelRouter()
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
        semantic_cache=None,
        event_bus=None,
        node_type: str | None = None,
        allowed_models: list[str] | None = None,
        routing_policy: dict[str, Any] | None = None,
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
        clone._semantic_cache = semantic_cache
        clone._event_bus = event_bus
        clone._node_type = node_type
        clone._allowed_models = list(
            allowed_models or DEFAULT_LLM_MODELS
        )
        clone._routing_policy = dict(routing_policy or {})
        clone._selection_history = []
        clone._call_sequence = 0
        return clone

    @property
    def selection_history(self) -> list[dict[str, Any]]:
        """Safe copy of model decisions made inside the bound node call."""

        return [dict(item) for item in self._selection_history]

    def _record_cost(
        self,
        intended: str,
        selected: str,
        resolved: str,
        resp,
        decision: ModelSelection,
    ) -> None:
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
                selected_model=selected,
                input_tokens=getattr(resp, "input_tokens",  0) or 0,
                output_tokens=getattr(resp, "output_tokens", 0) or 0,
                cost_usd=CostLedger.calculate(
                    resolved,
                    getattr(resp, "input_tokens",  0) or 0,
                    getattr(resp, "output_tokens", 0) or 0,
                ),
                selection_mode=decision.mode,
                selection_reason=decision.reason,
                task_kind=decision.task_kind,
                complexity=decision.complexity,
                cache_hit=bool(getattr(resp, "cache_hit", False)),
            )
            self._ledger.record(entry)
            metrics.LLM_COST_USD.labels(model=resolved).inc(entry.cost_usd)
        except Exception:
            pass  # cost tracking must never break the actual LLM call

    def _models_for_call(self, intended: str) -> list[str]:
        """Return configured primary/fallback providers without duplicates."""

        primary = resolve_model(intended)
        candidates = [primary]
        fallback = _FALLBACK_MODEL.get(intended)
        if (
            fallback
            and fallback != primary
            and fallback in self._allowed_models
        ):
            candidates.append(fallback)
        available = [
            model
            for model in candidates
            if _provider_is_configured(model)
        ]
        if not available:
            raise LLMProviderUnavailableError(
                f"No configured provider is available for {intended!r}. "
                "Set the corresponding provider API key."
            )
        return list(dict.fromkeys(available))

    async def _publish_selection(
        self,
        decision: ModelSelection,
        *,
        actual_model: str,
        call_id: int,
        fallback: bool = False,
        cache_hit: bool = False,
    ) -> None:
        """Record and stream one auditable provider choice."""

        selection = decision.to_event(
            actual_model=actual_model,
            call_id=call_id,
            fallback=fallback,
            cache_hit=cache_hit,
        )
        self._selection_history.append(selection)
        log.info(
            "llm.model_selected",
            run_id=self._run_id,
            node_id=self._node_id,
            **selection,
        )
        if self._event_bus is not None and self._run_id:
            from app.runtime.events import RunEvent

            await self._event_bus.publish(
                RunEvent(
                    type="model_selected",
                    run_id=self._run_id,
                    node_id=self._node_id,
                    context=selection,
                )
            )

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
    ) -> tuple[Any, str, ModelSelection]:
        requested_model = intended
        intended, kwargs, input_tokens, cost_protection = await self._prepare_call(
            method_name,
            intended,
            kwargs,
        )
        if intended == AUTO_MODEL:
            try:
                decision = self._model_router.select(
                    method_name=method_name,
                    kwargs=kwargs,
                    input_tokens=input_tokens,
                    allowed_models=self._allowed_models,
                    is_available=_provider_is_configured,
                    node_type=self._node_type,
                    policy=self._routing_policy,
                )
            except ModelRoutingError as exc:
                raise LLMProviderUnavailableError(str(exc)) from exc
        else:
            decision = self._model_router.describe_manual(
                requested_model=requested_model,
                selected_model=intended,
                method_name=method_name,
                kwargs=kwargs,
                input_tokens=input_tokens,
                node_type=self._node_type,
                mode=(
                    "cost_protection"
                    if cost_protection
                    else "manual"
                ),
            )
        selected_model = decision.selected_model
        candidates = self._models_for_call(selected_model)
        last_error: BaseException | None = None
        cache_lookup = None
        self._call_sequence += 1
        call_id = self._call_sequence

        if (
            method_name == "complete"
            and self._semantic_cache is not None
            and settings.semantic_cache_enabled
            and float(kwargs.get("temperature", 0.0)) == 0.0
            and self._session_id
        ):
            cache_lookup = await self._semantic_cache.get(
                session_id=self._session_id,
                model=selected_model,
                system=str(kwargs.get("system", "")),
                user=str(kwargs.get("user", "")),
                temperature=float(kwargs.get("temperature", 0.0)),
                max_tokens=int(kwargs.get("max_tokens", 1_024)),
            )
            if cache_lookup.hit and cache_lookup.response:
                cached = cache_lookup.response
                cached_model = str(
                    cached.get("model") or candidates[0]
                )
                await self._publish_selection(
                    decision,
                    actual_model=cached_model,
                    call_id=call_id,
                    cache_hit=True,
                )
                if self._event_bus is not None and self._run_id:
                    from app.runtime.events import RunEvent

                    await self._event_bus.publish(
                        RunEvent(
                            type="llm_token",
                            run_id=self._run_id,
                            node_id=self._node_id,
                            token=str(cached["text"]),
                            context={
                                "cache_hit": True,
                                "model": cached_model,
                            },
                        )
                    )
                return (
                    LLMResponse(
                        text=str(cached["text"]),
                        model=cached_model,
                        input_tokens=0,
                        output_tokens=0,
                        stop_reason=cached.get("stop_reason"),
                        cache_hit=True,
                        cache_similarity=cache_lookup.similarity,
                    ),
                    cached_model,
                    decision,
                )
        elif method_name == "complete":
            metrics.LLM_CACHE.labels(status="bypass").inc()

        for model_index, candidate in enumerate(candidates):
            gateway, resolved = get_gateway(candidate)
            await self._publish_selection(
                decision,
                actual_model=resolved,
                call_id=call_id,
                fallback=model_index > 0,
            )
            if model_index:
                metrics.LLM_FAILOVERS.labels(
                    from_model=candidates[0],
                    to_model=resolved,
                ).inc()
                log.warning(
                    "llm.failover",
                    intended=requested_model,
                    selected=selected_model,
                    from_model=candidates[0],
                    to_model=resolved,
                    run_id=self._run_id,
                    node_id=self._node_id,
                )

            for attempt in range(1, self._retry_policy.max_attempts + 1):
                call_started = time.perf_counter()
                try:
                    method = getattr(gateway, method_name)
                    call_kwargs = dict(kwargs)
                    if (
                        method_name == "complete"
                        and self._event_bus is not None
                        and self._run_id
                    ):
                        first_token = True

                        async def on_token(token: str) -> None:
                            nonlocal first_token
                            if first_token:
                                metrics.LLM_TIME_TO_FIRST_TOKEN.labels(
                                    model=resolved
                                ).observe(time.perf_counter() - call_started)
                                first_token = False
                            from app.runtime.events import RunEvent

                            await self._event_bus.publish(
                                RunEvent(
                                    type="llm_token",
                                    run_id=self._run_id or "unknown",
                                    node_id=self._node_id,
                                    token=token,
                                    context={
                                        "model": resolved,
                                        "attempt": attempt,
                                    },
                                )
                            )

                        call_kwargs["on_token"] = on_token
                    async with asyncio.timeout(
                        settings.llm_request_timeout_seconds
                    ):
                        response = await method(model=resolved, **call_kwargs)
                    metrics.LLM_LATENCY.labels(
                        model=resolved,
                        status="success",
                    ).observe(time.perf_counter() - call_started)
                    if (
                        method_name == "complete"
                        and self._semantic_cache is not None
                        and cache_lookup is not None
                        and not cache_lookup.hit
                        and self._session_id
                    ):
                        await self._semantic_cache.put(
                            session_id=self._session_id,
                            model=selected_model,
                            system=str(kwargs.get("system", "")),
                            user=str(kwargs.get("user", "")),
                            temperature=float(kwargs.get("temperature", 0.0)),
                            max_tokens=int(kwargs.get("max_tokens", 1_024)),
                            response={
                                "text": response.text,
                                "model": response.model,
                                "stop_reason": response.stop_reason,
                            },
                            query_embedding=cache_lookup.query_embedding,
                        )
                    return response, resolved, decision
                except Exception as exc:
                    metrics.LLM_LATENCY.labels(
                        model=resolved,
                        status="error",
                    ).observe(time.perf_counter() - call_started)
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
                            intended=requested_model,
                            selected=selected_model,
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
                            intended=requested_model,
                            selected=selected_model,
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
                        intended=requested_model,
                        selected=selected_model,
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

    async def _prepare_call(
        self,
        method_name: str,
        intended: str,
        kwargs: dict[str, Any],
    ) -> tuple[str, dict[str, Any], int, bool]:
        prepared = dict(kwargs)
        requested_output = int(prepared.get("max_tokens", 1_024))
        prepared["max_tokens"] = min(
            requested_output,
            settings.llm_max_output_tokens,
        )
        input_tokens = _estimate_input_tokens(method_name, prepared)
        if input_tokens > settings.llm_max_input_tokens:
            raise LLMInputLimitError(
                f"LLM input is approximately {input_tokens:,} tokens; "
                f"the configured limit is {settings.llm_max_input_tokens:,}"
            )

        cost_protection = False
        if (
            self._ledger is not None
            and self._session_id
            and self._session_id != "unknown"
        ):
            user_spend, global_spend = await asyncio.gather(
                asyncio.to_thread(
                    self._ledger.daily_spend,
                    self._session_id,
                ),
                asyncio.to_thread(self._ledger.daily_spend),
            )
            emergency = (
                user_spend >= settings.llm_user_daily_budget_usd
                or global_spend >= settings.llm_global_daily_budget_usd
            )
            if emergency:
                cost_protection = True
                if (
                    global_spend >= settings.llm_global_daily_budget_usd
                    and input_tokens
                    > settings.llm_emergency_max_input_tokens
                ):
                    raise LLMInputLimitError(
                        "The platform is in cost-protection mode and this "
                        f"request exceeds {settings.llm_emergency_max_input_tokens:,} "
                        "input tokens"
                    )
                log.warning(
                    "llm.cost_protection",
                    session_budget_reached=(
                        user_spend >= settings.llm_user_daily_budget_usd
                    ),
                    global_budget_reached=(
                        global_spend >= settings.llm_global_daily_budget_usd
                    ),
                    run_id=self._run_id,
                    node_id=self._node_id,
                )
                intended = settings.llm_emergency_model
        return intended, prepared, input_tokens, cost_protection

    async def complete(self, *, model: str, **kwargs):
        resp, resolved, decision = await self._call_resilient(
            "complete",
            intended=model,
            kwargs=kwargs,
        )
        _record_usage(decision.selected_model, resolved, resp)
        self._record_cost(
            model,
            decision.selected_model,
            resolved,
            resp,
            decision,
        )
        return resp

    async def complete_structured(self, *, model: str, **kwargs):
        resp, resolved, decision = await self._call_resilient(
            "complete_structured",
            intended=model,
            kwargs=kwargs,
        )
        # Concrete gateways return StructuredResult so cost survives native
        # structured output. The registry preserves the public bare-model API.
        _record_usage(decision.selected_model, resolved, resp)
        self._record_cost(
            model,
            decision.selected_model,
            resolved,
            resp,
            decision,
        )
        return resp.parsed

    async def chat_with_tools(self, *, model: str, **kwargs):
        resp, resolved, decision = await self._call_resilient(
            "chat_with_tools",
            intended=model,
            kwargs=kwargs,
        )
        _record_usage(decision.selected_model, resolved, resp)
        self._record_cost(
            model,
            decision.selected_model,
            resolved,
            resp,
            decision,
        )
        return resp


_default_registry_gateway: RegistryLLMGateway | None = None


def get_llm_gateway() -> RegistryLLMGateway:
    """Module-level singleton used by main.py's lifespan."""
    global _default_registry_gateway
    if _default_registry_gateway is None:
        _default_registry_gateway = RegistryLLMGateway()
    return _default_registry_gateway
