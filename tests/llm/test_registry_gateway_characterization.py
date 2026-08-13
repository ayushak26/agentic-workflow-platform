"""Characterization tests for app/llm/registry.py's RegistryLLMGateway.

RegistryLLMGateway is app.llm.get_llm_gateway()'s live implementation — it dispatches
directly to OpenRouter and the enterprise provider APIs (OpenAI, Anthropic). These tests pin
down what it verifiably does, reading the implementation in app/llm/registry.py directly.

Existing coverage this file deliberately does NOT duplicate:
  - tests/test_llm_resilience.py       (retry -> failover happy paths)
  - tests/test_model_router.py         (auto-routing / event-bus integration)
  - tests/llm/test_registry_tokenization_boundary.py (entity tokenization)

This file focuses on: pure routing/predicate helpers, the no-context fast
path for all three public methods, with_context() clone semantics, exact
retry-delay arithmetic, error-propagation, and local-model-probe wiring.

No real network calls are made anywhere in this file -- every concrete
provider gateway is replaced with an in-process fake before use.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import app.llm.registry as registry
from app.config import settings
from app.llm.base import LLMResponse
from app.llm.errors import LLMProviderUnavailableError, StructuredOutputError
from app.llm.local_openai_gw import LocalModelProfile
from app.llm.registry import RegistryLLMGateway, RetryPolicy


class _Answer(BaseModel):
    value: int


class RecordingGateway:
    """Fake concrete provider gateway -- records every call, never hits a network."""

    def __init__(self, response_text: str = "ok"):
        self.calls: list[tuple[str, str, dict]] = []
        self.response_text = response_text

    async def complete(self, *, model, **kwargs):
        self.calls.append(("complete", model, kwargs))
        return LLMResponse(text=self.response_text, model=model, input_tokens=10, output_tokens=5)

    async def complete_structured(self, *, model, response_model, **kwargs):
        self.calls.append(("complete_structured", model, {"response_model": response_model, **kwargs}))
        return SimpleNamespace(parsed=response_model(value=1), model=model, input_tokens=3, output_tokens=2)

    async def chat_with_tools(self, *, model, **kwargs):
        self.calls.append(("chat_with_tools", model, kwargs))
        return SimpleNamespace(text=self.response_text, tool_calls=[], model=model, input_tokens=4, output_tokens=1)


class FakeLedger:
    def __init__(self):
        self.entries: list = []

    def record(self, entry):
        self.entries.append(entry)


class FakeSemanticCache:
    def __init__(self, hit_response=None):
        self.hit_response = hit_response
        self.get_calls: list[dict] = []
        self.put_calls: list[dict] = []

    async def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return SimpleNamespace(
            hit=self.hit_response is not None,
            response=self.hit_response,
            query_embedding=None,
        )

    async def put(self, **kwargs):
        self.put_calls.append(kwargs)


# ── model -> gateway-class routing (pure functions) ──────────────────────────

@pytest.mark.parametrize(
    "model,expected_cls",
    [
        ("claude-opus-5", "AnthropicGateway"),
        ("claude-sonnet-4-5", "AnthropicGateway"),
        ("claude-fable-5", "AnthropicGateway"),
        ("gpt-5", "OpenAIGateway"),
        ("gpt-5.6-sol", "OpenAIGateway"),
        ("o3", "OpenAIGateway"),
        ("o4-mini", "OpenAIGateway"),
        ("local-kimi-k3", "KimiK3LocalGateway"),
        ("local-glm-5", "GLM5LocalGateway"),
    ],
)
def test_gateway_class_for_routes_known_prefixes(model, expected_cls):
    assert registry._gateway_class_for(model) is getattr(registry, expected_cls)


def test_gateway_class_for_raises_for_an_unrecognized_prefix():
    with pytest.raises(ValueError, match="No gateway registered"):
        registry._gateway_class_for("mystery-model-9000")


def test_gateway_class_for_rejects_a_non_llm_openai_endpoint():
    """text-embedding-3-small is a real catalog entry, but its `kind` is
    "embedding" -- the generic LLM gateway must never be routed to it."""
    with pytest.raises(ValueError, match="cannot be routed"):
        registry._gateway_class_for("text-embedding-3-small")


# ── resolve_model (pure function; stub-gateway fallback) ────────────────────

def test_resolve_model_returns_the_intended_name_when_not_stubbed():
    assert registry.resolve_model("claude-opus-5") == "claude-opus-5"


def test_resolve_model_falls_back_when_the_gateway_class_is_stubbed(monkeypatch):
    monkeypatch.setattr(registry, "_STUB_GATEWAYS", {registry.AnthropicGateway})
    assert registry.resolve_model("claude-opus-5") == "gpt-5.6-sol"


def test_resolve_model_raises_when_stubbed_with_no_mapped_fallback(monkeypatch):
    """resolve_model is purely prefix-driven -- it does not require the model
    to exist in any catalog, only that its prefix resolves to a gateway class."""
    monkeypatch.setattr(registry, "_STUB_GATEWAYS", {registry.AnthropicGateway})
    with pytest.raises(ValueError, match="no fallback mapped"):
        registry.resolve_model("claude-totally-unknown-model")


# ── get_gateway singleton caching ────────────────────────────────────────────

def test_get_gateway_caches_one_instance_per_gateway_class_not_per_model(monkeypatch):
    monkeypatch.setattr(registry, "_INSTANCES", {})
    built = []

    def fake_construct(gw_cls):
        instance = object()
        built.append(instance)
        return instance

    monkeypatch.setattr(registry, "_construct", fake_construct)

    gw1, resolved1 = registry.get_gateway("claude-opus-5")
    gw2, resolved2 = registry.get_gateway("claude-sonnet-4-5")

    assert resolved1 == "claude-opus-5"
    assert resolved2 == "claude-sonnet-4-5"
    assert gw1 is gw2, "all claude-* models share one AnthropicGateway singleton"
    assert len(built) == 1


# ── fast path (no with_context bound): complete / complete_structured / chat_with_tools ──

@pytest.mark.asyncio
async def test_complete_fast_path_delegates_to_the_resolved_provider_and_returns_llm_response(monkeypatch):
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    gateway = RegistryLLMGateway()

    resp = await gateway.complete(model="claude-opus-5", system="s", user="u")

    assert isinstance(resp, LLMResponse)
    assert resp.text == "ok"
    assert anthropic.calls == [("complete", "claude-opus-5", {"system": "s", "user": "u"})]


@pytest.mark.asyncio
async def test_complete_structured_fast_path_unwraps_to_the_parsed_model(monkeypatch):
    """The public API returns response_model instances directly, not the
    concrete gateway's StructuredResult/SimpleNamespace wrapper."""
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    gateway = RegistryLLMGateway()

    result = await gateway.complete_structured(
        model="claude-opus-5", system="s", user="u", response_model=_Answer
    )

    assert isinstance(result, _Answer)
    assert result.value == 1


@pytest.mark.asyncio
async def test_chat_with_tools_fast_path_returns_the_raw_provider_response_unwrapped(monkeypatch):
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    gateway = RegistryLLMGateway()

    result = await gateway.chat_with_tools(model="claude-opus-5", system="s", messages=[], tools=[])

    assert result.text == "ok"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_fast_path_call_never_populates_selection_history(monkeypatch):
    """selection_history is only written on the routing path (event_bus or
    allowed_models bound); the plain fast path used by scripts/most tests
    never touches it."""
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    gateway = RegistryLLMGateway()

    await gateway.complete(model="claude-opus-5", system="s", user="u")

    assert gateway.selection_history == []


@pytest.mark.asyncio
async def test_complete_fast_path_records_cost_with_both_intended_and_resolved_model(monkeypatch):
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    ledger = FakeLedger()
    gateway = RegistryLLMGateway().with_context(
        run_id="run1", session_id="sess1", node_id="node1", ledger=ledger
    )

    await gateway.complete(model="claude-opus-5", system="s", user="u")

    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry.model == "claude-opus-5"
    assert entry.intended_model == "claude-opus-5"
    assert entry.input_tokens == 10
    assert entry.output_tokens == 5


@pytest.mark.asyncio
async def test_complete_never_records_cost_without_a_bound_ledger(monkeypatch):
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    gateway = RegistryLLMGateway()

    # Must not raise despite no ledger bound -- absence of a ledger is a no-op.
    await gateway.complete(model="claude-opus-5", system="s", user="u")


# ── semantic cache integration on the fast path ──────────────────────────────

@pytest.mark.asyncio
async def test_semantic_cache_hit_skips_the_provider_call_entirely(monkeypatch):
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    monkeypatch.setattr(settings, "semantic_cache_enabled", True)
    cache = FakeSemanticCache(
        hit_response={"text": "cached", "model": "claude-opus-5", "input_tokens": 1, "output_tokens": 1}
    )
    gateway = RegistryLLMGateway().with_context(
        run_id="r", session_id="s", node_id="n", semantic_cache=cache, use_cache=True
    )

    resp = await gateway.complete(model="claude-opus-5", system="s", user="u")

    assert resp.text == "cached"
    assert anthropic.calls == []


@pytest.mark.asyncio
async def test_semantic_cache_miss_calls_the_provider_and_writes_through(monkeypatch):
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    monkeypatch.setattr(settings, "semantic_cache_enabled", True)
    cache = FakeSemanticCache(hit_response=None)
    gateway = RegistryLLMGateway().with_context(
        run_id="r", session_id="s", node_id="n", semantic_cache=cache, use_cache=True
    )

    await gateway.complete(model="claude-opus-5", system="s", user="u")

    assert len(anthropic.calls) == 1
    assert len(cache.put_calls) == 1
    assert cache.put_calls[0]["response"]["text"] == "ok"


# ── with_context() clone semantics ───────────────────────────────────────────

def test_with_context_accepts_the_exact_compiler_call_shape():
    gateway = RegistryLLMGateway()
    bound = gateway.with_context(
        "run_1",
        "sess_1",
        "node_1",
        None,
        event_bus=None,
        node_type="ai_task",
        allowed_models=["claude-opus-5"],
        routing_policy={"accuracy_priority": "high"},
        entity_tokenizer=object(),
        collection_id="corpus_a",
        processing_mode="pseudonymised",
    )
    assert bound._run_id == "run_1"
    assert bound._collection_id == "corpus_a"
    assert bound._processing_mode == "pseudonymised"


def test_with_context_returns_an_independent_clone_not_a_mutation():
    gateway = RegistryLLMGateway()
    a = gateway.with_context(run_id="run_a", session_id="s", node_id="n")
    b = gateway.with_context(run_id="run_b", session_id="s", node_id="n")

    assert a._run_id == "run_a"
    assert b._run_id == "run_b"
    assert gateway._run_id is None


@pytest.mark.asyncio
async def test_with_context_gives_each_clone_an_independent_selection_history(monkeypatch):
    anthropic = RecordingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: anthropic})
    base = RegistryLLMGateway()
    a = base.with_context(
        run_id="a", session_id="s", node_id="n", allowed_models=["claude-opus-5"]
    )
    b = base.with_context(
        run_id="b", session_id="s", node_id="n", allowed_models=["claude-opus-5"]
    )

    await a.complete(model="claude-opus-5", system="s", user="u")

    assert a.selection_history != []
    assert b.selection_history == []


# ── error propagation: the gateway does NOT normalize provider errors ───────
# (see app/llm/errors.py — only StructuredOutputError/LLMInputLimitError/
# LLMProviderUnavailableError/BatchTimeoutError/LLMPolicyDeniedError exist.)

class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_non_retryable_error_propagates_with_its_original_type_and_attributes(monkeypatch):
    primary = RecordingGateway()

    async def boom(*, model, **kwargs):
        raise ProviderError("bad credentials", 401)

    primary.complete = boom
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: primary})
    gateway = RegistryLLMGateway()

    with pytest.raises(ProviderError) as exc_info:
        await gateway.complete(model="claude-opus-5", system="s", user="u")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_structured_output_error_is_retried_then_raised_unmodified_when_exhausted(monkeypatch):
    primary = RecordingGateway()
    attempts = {"n": 0}

    async def flaky(*, model, response_model, **kwargs):
        attempts["n"] += 1
        raise StructuredOutputError("bad json")

    primary.complete_structured = flaky
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: primary})

    async def no_sleep(_delay):
        pass

    gateway = RegistryLLMGateway(
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0, jitter_ratio=0),
        sleep=no_sleep,
        # allowed_models restricted to the single model under test so this
        # never risks constructing a REAL fallback provider gateway.
    ).with_context(run_id="r", session_id="s", node_id="n", allowed_models=["claude-opus-5"])

    with pytest.raises(StructuredOutputError):
        await gateway.complete_structured(
            model="claude-opus-5", system="s", user="u", response_model=_Answer
        )

    assert attempts["n"] == 2


def test_models_for_call_raises_llm_provider_unavailable_error_when_allowed_models_exclude_everything():
    gateway = RegistryLLMGateway().with_context(
        run_id="r", session_id="s", node_id="n", allowed_models=["totally-different-model"]
    )
    with pytest.raises(LLMProviderUnavailableError, match="No permitted model remains"):
        gateway._models_for_call("claude-opus-5")


# ── retry delay arithmetic (_delay_for) ──────────────────────────────────────

def test_delay_for_honors_a_retry_after_header_capped_at_max_delay():
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=1, max_delay_seconds=2, jitter_ratio=0)
    gateway = RegistryLLMGateway(retry_policy=policy)
    exc = SimpleNamespace(response=SimpleNamespace(headers={"Retry-After": "10"}))

    assert gateway._delay_for(1, exc) == 2.0


def test_delay_for_exponential_backoff_without_jitter():
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=1, max_delay_seconds=100, jitter_ratio=0)
    gateway = RegistryLLMGateway(retry_policy=policy)
    exc = RuntimeError("boom")

    assert gateway._delay_for(1, exc) == 1.0
    assert gateway._delay_for(2, exc) == 2.0
    assert gateway._delay_for(3, exc) == 4.0


def test_delay_for_applies_symmetric_jitter_deterministically():
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=1, max_delay_seconds=100, jitter_ratio=0.2)
    gateway_high = RegistryLLMGateway(retry_policy=policy, random_value=lambda: 1.0)
    gateway_low = RegistryLLMGateway(retry_policy=policy, random_value=lambda: 0.0)

    assert gateway_high._delay_for(1, RuntimeError()) == pytest.approx(1.2)
    assert gateway_low._delay_for(1, RuntimeError()) == pytest.approx(0.8)


# ── RetryPolicy validation ────────────────────────────────────────────────────

def test_retry_policy_rejects_negative_delays():
    with pytest.raises(ValueError, match="cannot be negative"):
        RetryPolicy(base_delay_seconds=-1)


def test_retry_policy_rejects_out_of_range_jitter_ratio():
    with pytest.raises(ValueError, match="jitter_ratio"):
        RetryPolicy(jitter_ratio=1.5)


# ── _is_retryable_error (pure predicate) ─────────────────────────────────────

def test_is_retryable_error_for_structured_output_error():
    assert registry._is_retryable_error(StructuredOutputError("x")) is True


@pytest.mark.parametrize(
    "status,expected",
    [
        (408, True),
        (409, True),
        (429, True),
        (500, True),
        (599, True),
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (407, False),
        (600, False),
    ],
)
def test_is_retryable_error_by_status_code(status, expected):
    exc = SimpleNamespace(status_code=status)
    assert registry._is_retryable_error(exc) is expected


def test_is_retryable_error_for_timeout_and_connection_error_instances():
    assert registry._is_retryable_error(TimeoutError("t")) is True
    assert registry._is_retryable_error(ConnectionError("c")) is True


def test_is_retryable_error_for_anthropic_in_band_overload_event():
    """Anthropic sometimes streams an error as an SSE event after the HTTP
    response already returned 200 -- status_code alone would say "success"."""
    exc = SimpleNamespace(status_code=200, body={"error": {"type": "overloaded_error"}})
    assert registry._is_retryable_error(exc) is True


def test_is_retryable_error_falls_back_to_known_sdk_exception_class_names():
    rate_limit_error_cls = type("RateLimitError", (RuntimeError,), {})
    assert registry._is_retryable_error(rate_limit_error_cls("slow down")) is True


def test_is_retryable_error_is_false_for_an_unrecognized_plain_exception():
    assert registry._is_retryable_error(RuntimeError("nope")) is False


# ── _is_model_unavailable_error (pure predicate) ─────────────────────────────

@pytest.mark.parametrize("status", [403, 404])
def test_is_model_unavailable_error_for_403_and_404(status):
    assert registry._is_model_unavailable_error(SimpleNamespace(status_code=status)) is True


def test_is_model_unavailable_error_for_provider_reported_model_not_found_code():
    exc = SimpleNamespace(status_code=400, code="model_not_found", body=None, error=None)
    assert registry._is_model_unavailable_error(exc) is True


def test_is_model_unavailable_error_falls_back_to_a_message_substring_match():
    exc = RuntimeError("upstream said model_not_found for this deployment")
    assert registry._is_model_unavailable_error(exc) is True


def test_is_model_unavailable_error_excludes_authentication_failures():
    """401 deliberately does NOT count as model-unavailable -- switching
    models cannot repair bad credentials, so retries must not fail over."""
    assert registry._is_model_unavailable_error(SimpleNamespace(status_code=401)) is False


# ── _provider_error_code (pure extraction helper) ────────────────────────────

def test_provider_error_code_reads_a_direct_code_attribute():
    exc = SimpleNamespace(code="model_not_found", body=None, error=None)
    assert registry._provider_error_code(exc) == "model_not_found"


def test_provider_error_code_unwraps_a_nested_error_dict_inside_body():
    exc = SimpleNamespace(code=None, body={"error": {"code": "invalid_request_error"}}, error=None)
    assert registry._provider_error_code(exc) == "invalid_request_error"


def test_provider_error_code_reads_code_off_a_non_dict_error_object():
    exc = SimpleNamespace(code=None, body=None, error=SimpleNamespace(code="rate_limited"))
    assert registry._provider_error_code(exc) == "rate_limited"


def test_provider_error_code_returns_none_when_nothing_matches():
    assert registry._provider_error_code(RuntimeError("plain")) is None


# ── _retry_after_seconds (pure header parser) ────────────────────────────────

def test_retry_after_seconds_parses_a_numeric_header_value():
    exc = SimpleNamespace(response=SimpleNamespace(headers={"Retry-After": "3.5"}))
    assert registry._retry_after_seconds(exc) == 3.5


def test_retry_after_seconds_returns_none_when_no_headers_are_present():
    assert registry._retry_after_seconds(RuntimeError("no headers")) is None


def test_retry_after_seconds_returns_none_for_an_unparseable_value():
    exc = SimpleNamespace(response=SimpleNamespace(headers={"Retry-After": "not-a-number-or-date"}))
    assert registry._retry_after_seconds(exc) is None


def test_retry_after_seconds_falls_back_to_exc_headers_when_there_is_no_response():
    exc = SimpleNamespace(headers={"retry-after": "1"})
    assert registry._retry_after_seconds(exc) == 1.0


# ── _promote_tier_peers / _TIER_PEER (pure fallback-chain reordering) ────────

def test_promote_tier_peers_moves_a_distant_peer_directly_after_its_primary():
    result = registry._promote_tier_peers(["claude-opus-5", "gpt-5", "gpt-5.6-sol"])
    assert result == ["claude-opus-5", "gpt-5.6-sol", "gpt-5"]


def test_promote_tier_peers_leaves_an_already_adjacent_pair_untouched():
    requested = ["gpt-5.6-sol", "claude-opus-5", "gpt-5"]
    assert registry._promote_tier_peers(requested) == requested


def test_promote_tier_peers_ignores_a_peer_that_is_not_in_the_list():
    requested = ["claude-opus-5", "gpt-5"]
    assert registry._promote_tier_peers(requested) == requested


def test_tier_peer_mapping_for_fable_is_not_symmetric():
    """claude-fable-5's documented peer is gpt-5.6-sol, but gpt-5.6-sol's own
    peer maps back to claude-opus-5, not claude-fable-5. _promote_tier_peers'
    docstring claims the table is symmetric; this pairing is the exception."""
    assert registry._TIER_PEER["claude-fable-5"] == "gpt-5.6-sol"
    assert registry._TIER_PEER["gpt-5.6-sol"] == "claude-opus-5"


# ── configured_local_model_probes() ──────────────────────────────────────────

def test_configured_local_model_probes_is_empty_when_no_local_provider_is_enabled(monkeypatch):
    monkeypatch.setattr(settings, "local_kimi_enabled", False)
    monkeypatch.setattr(settings, "local_glm_enabled", False)

    assert registry.configured_local_model_probes() == {}


def test_configured_local_model_probes_exposes_the_enabled_local_gateways_probe(monkeypatch):
    profile = LocalModelProfile(
        alias="local-kimi-k3",
        provider="moonshot-local",
        enabled=True,
        base_url="http://localhost:8000",
        api_key="key",
        served_model="kimi-served",
        reasoning_effort="low",
        timeout_seconds=5,
        verify_served_model=False,
    )
    fake_kimi = registry.KimiK3LocalGateway(profile, client=SimpleNamespace())
    monkeypatch.setattr(registry, "_INSTANCES", {registry.KimiK3LocalGateway: fake_kimi})
    monkeypatch.setattr(settings, "local_kimi_enabled", True)
    monkeypatch.setattr(settings, "local_glm_enabled", False)

    probes = registry.configured_local_model_probes()

    assert set(probes) == {"llm:local-kimi-k3"}
    assert probes["llm:local-kimi-k3"] == fake_kimi.probe


# ── get_llm_gateway() module-level singleton ─────────────────────────────────

def test_get_llm_gateway_returns_the_same_singleton_across_calls(monkeypatch):
    monkeypatch.setattr(registry, "_default_registry_gateway", None)

    first = registry.get_llm_gateway()
    second = registry.get_llm_gateway()

    assert first is second
    assert isinstance(first, RegistryLLMGateway)
