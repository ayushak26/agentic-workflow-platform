"""Resolve a model name to the right gateway instance.

The active workflow catalog uses the approved OpenAI model registry. Optional
Anthropic and private OpenAI-compatible gateways remain available for legacy
or deployment-specific workflows, but automatic task routing only considers
capability-compatible OpenAI text-generation models.

The fallback layer decouples *intent* (what the YAML asks for) from
*runtime* (what's actually available). When the intended provider is
stubbed, we resolve to the closest live equivalent via a documented map.

Providers in scope:
  - Anthropic                                  — claude-*
  - OpenAI                                     — gpt-*
  - Private Moonshot-compatible endpoint       — local-kimi-*
  - Private Z.ai-compatible endpoint           — local-glm-*

The OpenAI o-series reasoning models (o1/o3/o4-mini) are intentionally not in
this catalog: they reject arbitrary `temperature` values (only the model
default is accepted), which auto-routing has no reason to special-case for,
and the business has marked them deprecated.
"""
from __future__ import annotations

import asyncio
import random
import time
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
from app.llm.model_catalog import AUTO_MODEL, MODEL_PROFILE_BY_NAME
from app.llm.model_router import ModelRouter, infer_task_kind
from app.llm.openai_gw import OpenAIGateway
from app.llm.openrouter_gw import OpenRouterGateway
from app.llm.openai_registry import (
    OPENAI_LLM_FALLBACK_CHAINS,
    OPENAI_MODEL_BY_NAME,
)
from app.llm.errors import LLMProviderUnavailableError, StructuredOutputError
from app.observability import metrics
from app.runtime.events import RunEvent
from app.security.entity_tokenizer import ProcessingMode

log = structlog.get_logger(__name__)

# Architectural routing — model prefix to gateway class. "openrouter/" must be
# checked before any other prefix could coincidentally match a suffix of it
# (none currently do, but list order is the tiebreak rule either way).
_PREFIX_ROUTES: list[tuple[str, type[LLMGateway]]] = [
    ("openrouter/", OpenRouterGateway),
    ("local-kimi-", KimiK3LocalGateway),
    ("local-glm-",  GLM5LocalGateway),
    ("claude-", AnthropicGateway),
    ("gpt-",    OpenAIGateway),
]

# Legacy provider fallbacks are retained for backward compatibility. The
# same-tier OpenAI equivalent leads each chain -- see _TIER_PEER below, which
# is the authoritative statement of these pairings and is also used to keep
# "auto" mode's degradation deterministic.
_LEGACY_FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    "claude-opus-5": ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5"),
    "claude-sonnet-4-5": ("gpt-5.6-terra", "gpt-5", "gpt-5-mini"),
    "claude-haiku-4-5": ("gpt-5.6-luna", "gpt-5-mini", "gpt-4o-mini"),
    "claude-opus-4-7": ("gpt-5.6-terra", "gpt-5"),
    "claude-opus-4-8": ("gpt-5.6-terra", "gpt-5"),
    "claude-fable-5": ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5"),
    # OpenAI -> Anthropic. A same-provider fallback alone is useless during a
    # full OpenAI outage, so the same-tier Claude model leads before the
    # existing OpenAI same-family degradation chain.
    "gpt-5.6-sol": (
        "claude-opus-5",
        *OPENAI_LLM_FALLBACK_CHAINS["gpt-5.6-sol"],
    ),
    "gpt-5.6-terra": (
        "claude-sonnet-4-5",
        *OPENAI_LLM_FALLBACK_CHAINS["gpt-5.6-terra"],
    ),
    "gpt-5.6-luna": (
        "claude-haiku-4-5",
        *OPENAI_LLM_FALLBACK_CHAINS["gpt-5.6-luna"],
    ),
}

# Complete ordered chains are used both after provider exhaustion and when a
# model-access failure (403/404/model_not_found) proves a candidate unusable.
_FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    **OPENAI_LLM_FALLBACK_CHAINS,
    **_LEGACY_FALLBACK_CHAINS,
}

# Compatibility view for callers/tests that need the immediate fallback.
_FALLBACK_MODEL: dict[str, str] = {
    model: chain[0]
    for model, chain in _FALLBACK_CHAINS.items()
    if chain
}

# Same-tier cross-provider equivalents ("gpt-5.6 is similar to Claude Opus/
# Sonnet/Haiku"). Used to promote each model's documented peer to the slot
# immediately after it in any runtime candidate list -- auto mode's own
# task-scoring already picks the best model irrespective of provider, but a
# provider-wide outage means "whatever scores next" is not a safe bet; the
# same-tier peer on the OTHER provider is.
_TIER_PEER: dict[str, str] = {
    "claude-opus-5": "gpt-5.6-sol",
    "gpt-5.6-sol": "claude-opus-5",
    "claude-sonnet-4-5": "gpt-5.6-terra",
    "gpt-5.6-terra": "claude-sonnet-4-5",
    "claude-haiku-4-5": "gpt-5.6-luna",
    "gpt-5.6-luna": "claude-haiku-4-5",
    "claude-fable-5": "gpt-5.6-sol",
}


def _promote_tier_peers(requested: list[str]) -> list[str]:
    """Move each model's documented cross-provider peer to directly follow it.

    Only reorders -- never adds or removes a candidate -- so a peer that
    isn't already permitted (not in allowed_models / not available) is left
    untouched. Each pair is settled once: since _TIER_PEER is symmetric,
    processing both directions would otherwise flip an already-adjacent,
    correctly-ordered pair back and forth.
    """

    result = list(requested)
    handled: set[frozenset[str]] = set()
    for model in requested:
        peer = _TIER_PEER.get(model)
        if peer is None or peer not in result:
            continue
        pair_key = frozenset((model, peer))
        if pair_key in handled:
            continue
        handled.add(pair_key)
        model_index = result.index(model)
        peer_index = result.index(peer)
        if peer_index <= model_index + 1:
            # Already adjacent in the right order, or the peer already
            # ranks earlier/better -- don't demote it.
            continue
        result.remove(peer)
        result.insert(result.index(model) + 1, peer)
    return result

# Gateway classes that are stubbed (not live).
_STUB_GATEWAYS: set[type[LLMGateway]] = set()

_INSTANCES: dict[type[LLMGateway], LLMGateway] = {}


@dataclass(frozen=True)
class RetryPolicy:
    """One explicit retry policy shared by all LLM providers.

    ``max_attempts`` includes the first request. After transient failures
    exhaust the primary model, the registry starts a fresh retry sequence on
    the mapped fallback. Validation/authentication errors fail immediately;
    model-access errors skip directly to the next candidate.
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


@dataclass(frozen=True)
class ModelAccessResult:
    """Result of a model-metadata probe that uses no generation tokens."""

    available: bool
    reason: str
    status_code: int | None = None
    cached: bool = False


def _status_code(exc: BaseException) -> int | None:
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _anthropic_error_type(exc: BaseException) -> str | None:
    """Anthropic streams some errors (e.g. overload) as an in-band SSE event
    after the HTTP response already returned 200, so the wrapping exception's
    status_code reflects the original 200, not the real error. Read the
    error type out of the response body instead."""

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            error_type = error.get("type")
            if isinstance(error_type, str):
                return error_type
    return None


def _is_retryable_error(exc: BaseException) -> bool:
    """Return whether another generation may succeed."""

    if isinstance(exc, StructuredOutputError):
        return True

    if isinstance(
        exc,
        (asyncio.TimeoutError, TimeoutError, ConnectionError),
    ):
        return True

    if _anthropic_error_type(exc) in {"overloaded_error", "api_error"}:
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


def _provider_error_code(exc: BaseException) -> str | None:
    """Extract a provider error code without depending on one SDK's shape."""

    pending: list[Any] = [
        getattr(exc, "code", None),
        getattr(exc, "body", None),
        getattr(exc, "error", None),
    ]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if value is None:
            continue
        if isinstance(value, str):
            if value.lower() == "model_not_found":
                return value
            continue
        value_id = id(value)
        if value_id in seen:
            continue
        seen.add(value_id)
        if isinstance(value, dict):
            code = value.get("code")
            if isinstance(code, str):
                return code
            pending.extend(
                value.get(key)
                for key in ("error", "detail", "body")
            )
            continue
        code = getattr(value, "code", None)
        if isinstance(code, str):
            return code
        pending.extend(
            getattr(value, key, None)
            for key in ("error", "detail", "body")
        )
    return None


def _is_model_unavailable_error(exc: BaseException) -> bool:
    """Return whether this candidate cannot serve the requested model.

    Retrying the same model cannot fix a permission or model-catalog mismatch,
    but another candidate may be usable. Authentication failures (401) are
    deliberately excluded because switching models does not repair bad
    credentials.
    """

    if _status_code(exc) in {403, 404}:
        return True
    code = (_provider_error_code(exc) or "").lower()
    if code == "model_not_found":
        return True
    return "model_not_found" in str(exc).lower()


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
    definition = OPENAI_MODEL_BY_NAME.get(model_name)
    if definition is not None and definition.kind != "llm":
        raise ValueError(
            f"OpenAI model {model_name!r} is registered for the "
            f"{definition.kind!r} endpoint and cannot be routed through "
            "the generic LLM gateway"
        )
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
    if gw_cls is OpenRouterGateway:
        return OpenRouterGateway(api_key=settings.openrouter_api_key)
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


#: Friendly provider label per gateway class, for cost telemetry (§30/§48).
#: Distinct from the model name itself — several models share one provider.
_PROVIDER_NAME_BY_GATEWAY: dict[type[LLMGateway], str] = {
    OpenAIGateway: "openai",
    AnthropicGateway: "anthropic",
    OpenRouterGateway: "openrouter",
    KimiK3LocalGateway: "moonshot-local",
    GLM5LocalGateway: "zai-local",
}


def _provider_name_for(model: str) -> str:
    try:
        return _PROVIDER_NAME_BY_GATEWAY.get(_gateway_class_for(model), "unknown")
    except ValueError:
        return "unknown"


def _safe_task_kind(
    method_name: str, kwargs: dict[str, Any], node_type: str | None
) -> str:
    """infer_task_kind(), but cost telemetry must never break a call."""
    try:
        return infer_task_kind(method_name, kwargs, node_type=node_type)
    except Exception:
        return "unknown"


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
        cache_write = getattr(resp, "cache_creation_input_tokens", 0) or 0
        cache_read  = getattr(resp, "cache_read_input_tokens",  0) or 0
        if cache_write:
            metrics.LLM_CACHE_TOKENS.labels(model=resolved, direction="write").inc(cache_write)
        if cache_read:
            metrics.LLM_CACHE_TOKENS.labels(model=resolved, direction="read").inc(cache_read)
    except Exception:
        pass


def _estimate_input_tokens(kwargs: dict[str, Any]) -> int:
    """Cheap pre-call token estimate for deterministic routing.

    The router needs an input size *before* the provider is called (the real
    token count only exists in the response). ~4 characters per token is the
    standard rough heuristic; the router only uses coarse thresholds
    (4k / 12k), so approximate is sufficient and, crucially, deterministic.
    """
    text = " ".join(
        str(kwargs.get(field, ""))
        for field in ("system", "user")
    )
    messages = kwargs.get("messages") or []
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                text += " " + str(message.get("content", ""))
    return max(0, len(text) // 4)


def _is_model_available(model: str) -> bool:
    """Return whether auto-routing may safely select ``model``.

    Automatic selection must not choose a provider that cannot be called.
    Tests and dependency-injected deployments may provide an instantiated
    gateway directly; otherwise cloud providers require credentials and local
    providers must be explicitly enabled by deployment configuration.
    """
    try:
        gateway_class = _gateway_class_for(model)
    except ValueError:
        return False
    if gateway_class in _STUB_GATEWAYS:
        return False
    if gateway_class in _INSTANCES:
        return True
    if gateway_class is OpenAIGateway:
        return bool(settings.openai_api_key)
    if gateway_class is AnthropicGateway:
        return bool(settings.anthropic_api_key)
    if gateway_class is OpenRouterGateway:
        return bool(settings.openrouter_api_key)
    if gateway_class is KimiK3LocalGateway:
        return settings.local_kimi_enabled
    if gateway_class is GLM5LocalGateway:
        return settings.local_glm_enabled
    return False


class RegistryLLMGateway(LLMGateway):
    """LLMGateway adapter that dispatches every call through the registry.

    Nodes consume a single LLMGateway instance via DI. This adapter doesn't
    bind to one provider — it routes per call. On every call it:
      1. Resolves the intended model name (applying fallback if needed)
      2. Delegates to the right concrete gateway
      3. Records token metrics + cost to the ledger

    When run under a bound context (see with_context) it also performs
    deterministic automatic model selection and publishes operator-visible
    model_selected / llm_token events to the run's event bus.

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
        # Automatic-routing context — bound per node by the compiler.
        self._event_bus = None            # RunEventBus | None
        self._node_type: str | None = None
        self._allowed_models: list[str] | None = None
        self._routing_policy: dict[str, Any] | None = None
        self._semantic_cache = None
        self._use_cache: bool = False
        # Confidential entity protection (Phase 1) — bound per node by the
        # compiler, same as event_bus/node_type above. None = no tokenizer
        # bound (e.g. a raw RegistryLLMGateway() built by a script/test) ->
        # tokenization is skipped entirely, preserving prior behavior.
        self._entity_tokenizer = None
        self._collection_id: str = "default"
        self._processing_mode: str | None = None
        # Shared by context-bound clones. Strict preflight populates this only
        # through provider model-metadata endpoints, never generation calls.
        self._model_access_cache: dict[
            str, tuple[float, ModelAccessResult]
        ] = {}
        self._model_access_lock = asyncio.Lock()
        # Per-instance selection record. The compiler lifts this into
        # state["model_selections"]; direct callers read it as a property.
        self._selection_history: list[dict[str, Any]] = []
        self._call_seq: int = 0
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
        *,
        event_bus=None,
        node_type: str | None = None,
        allowed_models: list[str] | None = None,
        routing_policy: dict[str, Any] | None = None,
        semantic_cache=None,
        use_cache: bool = False,
        entity_tokenizer=None,
        collection_id: str = "default",
        processing_mode: str | None = None,
    ) -> "RegistryLLMGateway":
        """Return a context-bound copy for per-node cost tracking and routing.

        Called by the compiler before handing the gateway to each node.
        Does not mutate the singleton — returns a shallow clone so parallel
        branches each get independent context. The new keyword-only routing
        arguments default to None, so existing positional callers
        (run_id, session_id, node_id, ledger) are unaffected and behave
        exactly as before.
        """
        clone = RegistryLLMGateway.__new__(RegistryLLMGateway)
        clone.__dict__.update(self.__dict__)
        clone._run_id     = run_id
        clone._session_id = session_id
        clone._node_id    = node_id
        clone._ledger     = ledger
        clone._event_bus       = event_bus
        clone._node_type       = node_type
        clone._allowed_models  = allowed_models
        clone._routing_policy  = routing_policy
        clone._semantic_cache  = semantic_cache
        clone._use_cache       = use_cache
        clone._entity_tokenizer = entity_tokenizer
        clone._collection_id    = collection_id
        clone._processing_mode  = processing_mode
        # Each bound clone accumulates its own selections independently.
        clone._selection_history = []
        clone._call_seq = 0
        return clone

    # ---- Confidential entity protection (Phase 1) --------------------------
    # Tokenization runs OUTSIDE _complete_impl/_complete_structured_impl/
    # _chat_with_tools_impl — i.e. before the semantic cache is ever touched
    # (SemanticLLMCache.get() calls an embedder on the raw `user` text before
    # the provider call), and before any provider/network call. See the
    # public complete()/complete_structured()/chat_with_tools() wrappers
    # below the renamed *_impl methods.

    async def _tokenize_text(self, text: str | None) -> str | None:
        if text is None or self._entity_tokenizer is None or self._session_id is None:
            return text
        result = await self._entity_tokenizer.tokenize(
            text,
            session_id=self._session_id,
            collection_id=self._collection_id,
            mode=ProcessingMode(self._processing_mode or ProcessingMode.PSEUDONYMISED.value),
        )
        return result.text

    async def _tokenize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return await self._tokenize_text(value)
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                result[key] = await self._tokenize_value(item)
            return result
        if isinstance(value, list):
            return [await self._tokenize_value(item) for item in value]
        return value

    async def _tokenize_messages(self, messages: list[dict]) -> list[dict]:
        tokenized: list[dict] = []
        for msg in messages:
            new_msg = dict(msg)
            content = new_msg.get("content")
            if isinstance(content, str):
                new_msg["content"] = await self._tokenize_text(content)
            tool_calls = new_msg.get("tool_calls")
            if isinstance(tool_calls, list):
                new_tool_calls = []
                for tc in tool_calls:
                    new_tc = dict(tc)
                    if isinstance(new_tc.get("arguments"), dict):
                        new_tc["arguments"] = await self._tokenize_value(
                            new_tc["arguments"]
                        )
                    new_tool_calls.append(new_tc)
                new_msg["tool_calls"] = new_tool_calls
            tokenized.append(new_msg)
        return tokenized

    async def _detokenize_value(self, value: Any) -> Any:
        if self._entity_tokenizer is None or self._session_id is None:
            return value
        result = await self._entity_tokenizer.detokenize(
            value,
            session_id=self._session_id,
            collection_id=self._collection_id,
        )
        return result.value

    def _cached_model_access(self, model: str) -> ModelAccessResult | None:
        cached = self._model_access_cache.get(model)
        if cached is None:
            return None
        checked_at, result = cached
        if (
            time.monotonic() - checked_at
            > settings.llm_model_access_cache_ttl_seconds
        ):
            self._model_access_cache.pop(model, None)
            return None
        return ModelAccessResult(
            available=result.available,
            reason=result.reason,
            status_code=result.status_code,
            cached=True,
        )

    def _available_for_auto(self, model: str) -> bool:
        """Combine configured-provider checks with verified project access."""

        if not _is_model_available(model):
            return False
        result = self._cached_model_access(model)
        return result.available if result is not None else True

    async def probe_model_access(
        self,
        models: list[str] | tuple[str, ...] | set[str],
    ) -> dict[str, ModelAccessResult]:
        """Probe concrete models without creating a completion.

        Cloud gateways use the provider's model-metadata endpoint. Local
        endpoints already have a dedicated readiness probe, so this method
        records only their configured state and lets that probe do the network
        check.
        """

        requested = sorted(
            {
                model
                for model in models
                if model and model != AUTO_MODEL
            }
        )
        results: dict[str, ModelAccessResult] = {}

        async with self._model_access_lock:
            for model in requested:
                cached = self._cached_model_access(model)
                if cached is not None:
                    results[model] = cached
                    continue

                if not _is_model_available(model):
                    result = ModelAccessResult(
                        available=False,
                        reason="provider is not configured or enabled",
                    )
                    self._model_access_cache[model] = (
                        time.monotonic(),
                        result,
                    )
                    results[model] = result
                    continue

                if is_local_model(model):
                    result = ModelAccessResult(
                        available=True,
                        reason=(
                            "local endpoint is configured; its readiness "
                            "probe runs separately"
                        ),
                    )
                    self._model_access_cache[model] = (
                        time.monotonic(),
                        result,
                    )
                    results[model] = result
                    continue

                try:
                    gateway, resolved = get_gateway(model)
                    probe = getattr(gateway, "probe_model_access", None)
                    if probe is None:
                        # Dependency-injected gateways used by tests and
                        # private deployments may not expose provider metadata.
                        result = ModelAccessResult(
                            available=True,
                            reason="injected gateway has no metadata probe",
                        )
                    else:
                        await asyncio.wait_for(
                            probe(resolved),
                            timeout=(
                                settings
                                .llm_model_access_probe_timeout_seconds
                            ),
                        )
                        result = ModelAccessResult(
                            available=True,
                            reason="provider confirmed model access",
                        )
                except Exception as exc:
                    status = _status_code(exc)
                    if status in {401, 403, 404}:
                        reason = (
                            "provider rejected this model for the configured "
                            f"project (HTTP {status})"
                        )
                    else:
                        reason = (
                            "model metadata probe failed: "
                            f"{type(exc).__name__}"
                        )
                    result = ModelAccessResult(
                        available=False,
                        reason=reason,
                        status_code=status,
                    )

                self._model_access_cache[model] = (
                    time.monotonic(),
                    result,
                )
                results[model] = result

        return results

    @property
    def selection_history(self) -> list[dict[str, Any]]:
        """Per-call model-selection events recorded on this bound gateway.

        Contains no prompt or generated content — safe for operator UI and
        for lifting into workflow state.
        """
        return self._selection_history

    def _next_call_id(self) -> int:
        self._call_seq += 1
        return self._call_seq

    def _select_model(
        self,
        *,
        method_name: str,
        requested: str,
        kwargs: dict[str, Any],
    ):
        """Deterministic, zero-token model choice for one call.

        AUTO_MODEL -> ModelRouter.select over the node's allowed_models and
        routing policy. Any explicit model -> describe_manual, so the event
        stream still shows what ran and why, uniformly for auto and manual.
        """
        router = ModelRouter()
        input_tokens = _estimate_input_tokens(kwargs)
        if requested == AUTO_MODEL:
            allowed = self._allowed_models or list(MODEL_PROFILE_BY_NAME.keys())
            return router.select(
                method_name=method_name,
                kwargs=kwargs,
                input_tokens=input_tokens,
                allowed_models=allowed,
                is_available=self._available_for_auto,
                node_type=self._node_type,
                policy=self._routing_policy,
            )
        return router.describe_manual(
            requested_model=requested,
            selected_model=requested,
            method_name=method_name,
            kwargs=kwargs,
            input_tokens=input_tokens,
            node_type=self._node_type,
        )

    async def _publish(self, evt: RunEvent) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(evt)

    def _record_cost(
        self,
        intended: str,
        resolved: str,
        resp,
        *,
        task_type: str = "unknown",
        latency_ms: float | None = None,
        fallback_reason: str | None = None,
        stage: str | None = None,
    ) -> None:
        """Write a LedgerEntry if we have context + a ledger."""
        if self._ledger is None:
            return
        try:
            from app.observability.cost_ledger import CostLedger, LedgerEntry
            input_tokens = getattr(resp, "input_tokens", 0) or 0
            output_tokens = getattr(resp, "output_tokens", 0) or 0
            cache_creation = getattr(resp, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(resp, "cache_read_input_tokens", 0) or 0
            # OpenRouter reports a real, authoritative per-call cost (usage.cost) that
            # covers all ~500 of its models — Eurskem's own MODEL_PRICING table has no
            # entry for any of them and would silently fall back to a generic default.
            # Direct providers (OpenAI/Anthropic) don't report cost, so resp.cost_usd is
            # None there and the estimate is the only option. Never treat 0.0 as "no
            # value reported" -- a genuinely free completion is a real, meaningful cost.
            authoritative_cost = getattr(resp, "cost_usd", None)
            cost_usd = (
                authoritative_cost
                if authoritative_cost is not None
                else CostLedger.calculate(
                    resolved,
                    input_tokens,
                    output_tokens,
                    cache_creation_input_tokens=cache_creation,
                    cache_read_input_tokens=cache_read,
                )
            )
            entry = LedgerEntry(
                run_id=self._run_id or "unknown",
                session_id=self._session_id or "unknown",
                node_id=self._node_id or "unknown",
                model=resolved,
                intended_model=intended,          # audit: what the YAML asked for
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=cache_creation,
                cache_read_input_tokens=cache_read,
                cost_usd=cost_usd,
                cost_source="provider_reported" if authoritative_cost is not None else "estimated",
                task_type=task_type,
                provider=_provider_name_for(resolved),
                latency_ms=latency_ms,
                fallback_used=resolved != intended,
                fallback_reason=fallback_reason,
                stage=stage,
            )
            self._ledger.record(entry)
        except Exception:
            pass  # cost tracking must never break the actual LLM call

    def _models_for_call(
        self,
        intended: str,
        candidate_models: tuple[str, ...] | list[str] | None = None,
    ) -> list[str]:
        """Return the permitted, ordered runtime fallback chain.

        Auto routing supplies its complete scored candidate list. Manual and
        legacy calls retain the static provider fallback. Filtering happens
        before model resolution so a fallback can never escape the node's
        ``allowed_models`` boundary.
        """

        requested = list(candidate_models or [intended])
        if intended not in requested:
            requested.insert(0, intended)
        if candidate_models is None:
            for static_fallback in _FALLBACK_CHAINS.get(intended, ()):
                if static_fallback not in requested:
                    requested.append(static_fallback)
        requested = _promote_tier_peers(requested)

        allowed = (
            set(self._allowed_models)
            if self._allowed_models is not None
            else None
        )
        candidates: list[str] = []
        for model in requested:
            if model == AUTO_MODEL:
                continue
            if allowed is not None and model not in allowed:
                continue
            resolved = resolve_model(model)
            if resolved not in candidates:
                candidates.append(resolved)

        if not candidates:
            raise LLMProviderUnavailableError(
                "No permitted model remains in the runtime fallback chain."
            )
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
        candidate_models: tuple[str, ...] | list[str] | None = None,
    ) -> tuple[Any, str, float, str | None]:
        """Returns (response, resolved_model, latency_ms, fallback_reason).

        ``fallback_reason`` is None on the first candidate; when a later
        candidate is the one that actually succeeds, it summarizes why the
        earlier candidate(s) were skipped — for cost-telemetry visibility
        (§30/§32), not just a log line.
        """
        candidates = self._models_for_call(
            intended,
            candidate_models=candidate_models,
        )
        last_error: BaseException | None = None
        last_failure_reason: str | None = None
        started = time.monotonic()

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
                    latency_ms = round((time.monotonic() - started) * 1000, 1)
                    fallback_reason = last_failure_reason if model_index else None
                    return response, resolved, latency_ms, fallback_reason
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    retryable = _is_retryable_error(exc)
                    model_unavailable = _is_model_unavailable_error(exc)
                    metrics.LLM_CALLS.labels(
                        model=resolved,
                        status="error",
                    ).inc()
                    exhausted = attempt >= self._retry_policy.max_attempts

                    if model_unavailable:
                        last_failure_reason = (
                            f"{candidate} unavailable ({type(exc).__name__}"
                            f"{f', status {_status_code(exc)}' if _status_code(exc) else ''})"
                        )
                        unavailable_result = ModelAccessResult(
                            available=False,
                            reason=(
                                "runtime call confirmed this model is "
                                "unavailable"
                            ),
                            status_code=_status_code(exc),
                        )
                        for unavailable_model in {candidate, resolved}:
                            self._model_access_cache[unavailable_model] = (
                                time.monotonic(),
                                unavailable_result,
                            )
                        log.warning(
                            "llm.model_unavailable",
                            intended=intended,
                            resolved=resolved,
                            status_code=_status_code(exc),
                            provider_error_code=_provider_error_code(exc),
                            has_fallback=model_index + 1 < len(candidates),
                            error_type=type(exc).__name__,
                            run_id=self._run_id,
                            node_id=self._node_id,
                        )
                        break

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

                    last_failure_reason = (
                        f"{candidate} exhausted retries ({type(exc).__name__}"
                        f"{f', status {_status_code(exc)}' if _status_code(exc) else ''})"
                    )
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

    async def complete(self, *, model: str, stage: str | None = None, **kwargs):
        """Public entry point — tokenizes system/user, delegates to the
        unchanged implementation, then detokenizes the response. Skipped
        entirely (zero behavior change) when no entity_tokenizer/session_id
        is bound — see with_context().

        ``stage`` optionally tags this call within a larger pipeline (e.g.
        "rerank"/"compress"/"generation" inside a RAG node) for cost
        telemetry — it never reaches a provider API."""
        if self._entity_tokenizer is None or self._session_id is None:
            return await self._complete_impl(model=model, stage=stage, **kwargs)
        tokenized = dict(kwargs)
        tokenized["system"] = await self._tokenize_text(kwargs.get("system", ""))
        tokenized["user"] = await self._tokenize_text(kwargs.get("user", ""))
        resp = await self._complete_impl(model=model, stage=stage, **tokenized)
        detokenized_text = await self._detokenize_value(resp.text)
        return resp.model_copy(update={"text": detokenized_text})

    async def _complete_impl(self, *, model: str, stage: str | None = None, **kwargs):
        # Fast path: no routing context bound (executor's plain calls, scripts,
        # and the existing test suite). Behaviour is exactly as before —
        # no selection, no events, no on_token injection.
        if self._event_bus is None and self._allowed_models is None:
            cache = self._semantic_cache
            use_cache = (
                self._use_cache
                and cache is not None
                and settings.semantic_cache_enabled
                and self._session_id is not None
                and kwargs.get("on_token") is None
            )
            lookup = None
            if use_cache:
                lookup = await cache.get(
                    session_id=self._session_id,
                    model=resolve_model(model),
                    system=kwargs.get("system", ""),
                    user=kwargs.get("user", ""),
                    temperature=float(kwargs.get("temperature", 0.0)),
                    max_tokens=int(kwargs.get("max_tokens", 1024)),
                )
                if lookup.hit and lookup.response is not None:
                    from app.llm.base import LLMResponse

                    return LLMResponse(**lookup.response)
            resp, resolved, latency_ms, fallback_reason = await self._call_resilient(
                "complete",
                intended=model,
                kwargs=kwargs,
            )
            _record_usage(model, resolved, resp)
            self._record_cost(
                model,
                resolved,
                resp,
                task_type=_safe_task_kind("complete", kwargs, self._node_type),
                latency_ms=latency_ms,
                fallback_reason=fallback_reason,
                stage=stage,
            )
            if use_cache:
                await cache.put(
                    session_id=self._session_id,
                    model=resolved,
                    system=kwargs.get("system", ""),
                    user=kwargs.get("user", ""),
                    temperature=float(kwargs.get("temperature", 0.0)),
                    max_tokens=int(kwargs.get("max_tokens", 1024)),
                    response=resp.model_dump(),
                    query_embedding=(
                        lookup.query_embedding if lookup is not None else None
                    ),
                )
            return resp

        # Routing path: choose a model deterministically, announce it, then
        # stream tokens. Streaming is applied ONLY here — complete_structured
        # and chat_with_tools never receive on_token because streaming
        # corrupts tool_use output.
        selection = self._select_model(
            method_name="complete",
            requested=model,
            kwargs=kwargs,
        )
        intended = selection.selected_model
        call_id = self._next_call_id()

        # Publish model_selected BEFORE the provider call so the event order is
        # deterministic: model_selected precedes any llm_token. actual_model is
        # the resolved (fallback-aware) name of the primary candidate.
        resolved_preview = resolve_model(intended)
        event_dict = selection.to_event(
            actual_model=resolved_preview,
            call_id=call_id,
        )
        self._selection_history.append(event_dict)
        await self._publish(
            RunEvent(
                type="model_selected",
                run_id=self._run_id or "unknown",
                session_id=self._session_id,
                node_id=self._node_id,
                context=event_dict,
            )
        )

        async def _emit_token(token: str) -> None:
            await self._publish(
                RunEvent(
                    type="llm_token",
                    run_id=self._run_id or "unknown",
                    session_id=self._session_id,
                    node_id=self._node_id,
                    token=token,
                )
            )

        call_kwargs = dict(kwargs)
        call_kwargs["on_token"] = _emit_token
        resp, resolved, latency_ms, fallback_reason = await self._call_resilient(
            "complete",
            intended=intended,
            kwargs=call_kwargs,
            candidate_models=(
                selection.candidate_models
                if model == AUTO_MODEL
                else None
            ),
        )
        event_dict["actual_model"] = resolved
        event_dict["fallback"] = resolved != resolved_preview
        _record_usage(intended, resolved, resp)
        self._record_cost(
            intended,
            resolved,
            resp,
            task_type=selection.task_kind,
            latency_ms=latency_ms,
            fallback_reason=fallback_reason,
            stage=stage,
        )
        return resp

    async def complete_structured(self, *, model: str, stage: str | None = None, **kwargs):
        """Public entry point — tokenizes system/user, delegates, then
        detokenizes every string field of the parsed structured result by
        walking its model_dump() and re-validating. Skipped entirely when no
        entity_tokenizer/session_id is bound."""
        if self._entity_tokenizer is None or self._session_id is None:
            return await self._complete_structured_impl(model=model, stage=stage, **kwargs)
        tokenized = dict(kwargs)
        tokenized["system"] = await self._tokenize_text(kwargs.get("system", ""))
        tokenized["user"] = await self._tokenize_text(kwargs.get("user", ""))
        parsed = await self._complete_structured_impl(model=model, stage=stage, **tokenized)
        response_model = type(parsed)
        walked = await self._detokenize_value(parsed.model_dump())
        return response_model.model_validate(walked)

    async def _complete_structured_impl(self, *, model: str, stage: str | None = None, **kwargs):
        if self._event_bus is None and self._allowed_models is None:
            resp, resolved, latency_ms, fallback_reason = await self._call_resilient(
                "complete_structured",
                intended=model,
                kwargs=kwargs,
            )
            _record_usage(model, resolved, resp)
            self._record_cost(
                model,
                resolved,
                resp,
                task_type=_safe_task_kind("complete_structured", kwargs, self._node_type),
                latency_ms=latency_ms,
                fallback_reason=fallback_reason,
                stage=stage,
            )
            return resp.parsed

        selection = self._select_model(
            method_name="complete_structured",
            requested=model,
            kwargs=kwargs,
        )
        intended = selection.selected_model
        call_id = self._next_call_id()
        resolved_preview = resolve_model(intended)
        event_dict = selection.to_event(
            actual_model=resolved_preview,
            call_id=call_id,
        )
        self._selection_history.append(event_dict)
        await self._publish(
            RunEvent(
                type="model_selected",
                run_id=self._run_id or "unknown",
                session_id=self._session_id,
                node_id=self._node_id,
                context=event_dict,
            )
        )
        resp, resolved, latency_ms, fallback_reason = await self._call_resilient(
            "complete_structured",
            intended=intended,
            kwargs=kwargs,
            candidate_models=(
                selection.candidate_models
                if model == AUTO_MODEL
                else None
            ),
        )
        event_dict["actual_model"] = resolved
        event_dict["fallback"] = resolved != resolved_preview
        # Concrete gateways return StructuredResult so cost survives native
        # structured output. The registry preserves the public bare-model API.
        _record_usage(intended, resolved, resp)
        self._record_cost(
            intended,
            resolved,
            resp,
            task_type=selection.task_kind,
            latency_ms=latency_ms,
            fallback_reason=fallback_reason,
            stage=stage,
        )
        return resp.parsed

    async def chat_with_tools(self, *, model: str, **kwargs):
        """Public entry point — tokenizes system + every message's content/
        tool_calls[].arguments, delegates, then detokenizes text/
        reasoning_content/tool_calls[].arguments on the response. Skipped
        entirely when no entity_tokenizer/session_id is bound."""
        if self._entity_tokenizer is None or self._session_id is None:
            return await self._chat_with_tools_impl(model=model, **kwargs)
        tokenized = dict(kwargs)
        tokenized["system"] = await self._tokenize_text(kwargs.get("system", ""))
        tokenized["messages"] = await self._tokenize_messages(
            kwargs.get("messages", [])
        )
        resp = await self._chat_with_tools_impl(model=model, **tokenized)
        detokenized_text = await self._detokenize_value(resp.text)
        detokenized_reasoning = await self._detokenize_value(resp.reasoning_content)
        detokenized_tool_calls = []
        for tool_call in resp.tool_calls:
            detokenized_args = await self._detokenize_value(tool_call.arguments)
            detokenized_tool_calls.append(
                tool_call.model_copy(update={"arguments": detokenized_args})
            )
        return resp.model_copy(
            update={
                "text": detokenized_text,
                "reasoning_content": detokenized_reasoning,
                "tool_calls": detokenized_tool_calls,
            }
        )

    async def _chat_with_tools_impl(self, *, model: str, **kwargs):
        if self._event_bus is None and self._allowed_models is None:
            resp, resolved, latency_ms, fallback_reason = await self._call_resilient(
                "chat_with_tools",
                intended=model,
                kwargs=kwargs,
            )
            _record_usage(model, resolved, resp)
            self._record_cost(
                model,
                resolved,
                resp,
                task_type=_safe_task_kind("chat_with_tools", kwargs, self._node_type),
                latency_ms=latency_ms,
                fallback_reason=fallback_reason,
            )
            return resp

        selection = self._select_model(
            method_name="chat_with_tools",
            requested=model,
            kwargs=kwargs,
        )
        intended = selection.selected_model
        call_id = self._next_call_id()
        resolved_preview = resolve_model(intended)
        event_dict = selection.to_event(
            actual_model=resolved_preview,
            call_id=call_id,
        )
        self._selection_history.append(event_dict)
        await self._publish(
            RunEvent(
                type="model_selected",
                run_id=self._run_id or "unknown",
                session_id=self._session_id,
                node_id=self._node_id,
                context=event_dict,
            )
        )
        resp, resolved, latency_ms, fallback_reason = await self._call_resilient(
            "chat_with_tools",
            intended=intended,
            kwargs=kwargs,
            candidate_models=(
                selection.candidate_models
                if model == AUTO_MODEL
                else None
            ),
        )
        event_dict["actual_model"] = resolved
        event_dict["fallback"] = resolved != resolved_preview
        _record_usage(intended, resolved, resp)
        self._record_cost(
            intended,
            resolved,
            resp,
            task_type=selection.task_kind,
            latency_ms=latency_ms,
            fallback_reason=fallback_reason,
        )
        return resp


_default_registry_gateway: RegistryLLMGateway | None = None


def get_llm_gateway() -> RegistryLLMGateway:
    """Module-level singleton used by main.py's lifespan."""
    global _default_registry_gateway
    if _default_registry_gateway is None:
        _default_registry_gateway = RegistryLLMGateway()
    return _default_registry_gateway
