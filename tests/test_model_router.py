from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import app.llm.registry as registry
from app.llm.base import LLMResponse
from app.llm.model_catalog import AUTO_MODEL, DEFAULT_LLM_MODELS
from app.llm.model_router import ModelRouter, ModelRoutingError
from app.llm.registry import RegistryLLMGateway, RetryPolicy
from app.runtime.executor import run_workflow
from app.runtime.events import RunEventBus
from app.runtime.loader import load_workflow_from_string


def _all_available(_model: str) -> bool:
    return True


def _cloud_available(model: str) -> bool:
    return not model.startswith("local-")


def test_simple_extraction_uses_an_economy_model():
    decision = ModelRouter().select(
        method_name="complete",
        kwargs={
            "system": "Extract the title and author.",
            "user": "A short document",
            "max_tokens": 300,
        },
        input_tokens=120,
        allowed_models=DEFAULT_LLM_MODELS,
        is_available=_cloud_available,
        node_type="TransformAgent",
    )

    assert decision.mode == "auto"
    assert decision.complexity == "simple"
    assert decision.task_kind == "extraction"
    assert decision.selected_model == "gpt-4o-mini"


def test_complex_proposal_writing_uses_writing_strength():
    decision = ModelRouter().select(
        method_name="complete",
        kwargs={
            "system": (
                "Draft a Horizon Europe proposal methodology with scientific "
                "evidence, policy compliance, risks, and trade-offs."
            ),
            "user": "x" * 40_000,
            "max_tokens": 8_000,
        },
        input_tokens=15_000,
        allowed_models=DEFAULT_LLM_MODELS,
        is_available=_cloud_available,
        node_type="ConceptAlternativesAgent",
    )

    assert decision.complexity == "complex"
    assert decision.task_kind == "writing"
    assert decision.selected_model == "claude-opus-5"


def test_complex_structured_request_uses_structured_strength():
    decision = ModelRouter().select(
        method_name="complete_structured",
        kwargs={
            "system": "Evaluate the scientific evidence and return the schema.",
            "user": "x" * 40_000,
            "max_tokens": 8_000,
            "response_model": object(),
        },
        input_tokens=15_000,
        allowed_models=DEFAULT_LLM_MODELS,
        is_available=_cloud_available,
        node_type="HorizonEvaluationAgent",
    )

    assert decision.task_kind == "structured"
    assert decision.selected_model == "gpt-5.6-sol"


def test_offline_quality_scores_override_generic_policy():
    quality_scores = {
        model: (0.99 if model == "gpt-5" else 0.20)
        for model in DEFAULT_LLM_MODELS
    }
    decision = ModelRouter().select(
        method_name="complete",
        kwargs={
            "system": "Draft a short summary.",
            "user": "content",
            "max_tokens": 500,
        },
        input_tokens=200,
        allowed_models=DEFAULT_LLM_MODELS,
        is_available=_all_available,
        policy={"quality_scores": quality_scores},
    )

    assert decision.selected_model == "gpt-5"
    assert decision.score_source == "evaluation"


def test_per_call_cost_ceiling_is_enforced():
    with pytest.raises(ModelRoutingError, match="lowest estimate"):
        ModelRouter().select(
            method_name="complete",
            kwargs={
                "system": "Draft a long proposal.",
                "user": "content",
                "max_tokens": 8_000,
            },
            input_tokens=20_000,
            allowed_models=DEFAULT_LLM_MODELS,
            is_available=_cloud_available,
            policy={"max_estimated_cost_usd": 0.000001},
        )


def test_enabled_local_models_participate_in_auto_routing():
    writing = ModelRouter().select(
        method_name="complete",
        kwargs={
            "system": "Draft a Horizon Europe proposal.",
            "user": "x" * 40_000,
            "max_tokens": 8_000,
        },
        input_tokens=15_000,
        allowed_models=DEFAULT_LLM_MODELS,
        is_available=_all_available,
        node_type="TransformAgent",
    )
    structured = ModelRouter().select(
        method_name="complete_structured",
        kwargs={
            "system": "Verify evidence and return the schema.",
            "user": "x" * 40_000,
            "max_tokens": 8_000,
            "response_model": object(),
        },
        input_tokens=15_000,
        allowed_models=DEFAULT_LLM_MODELS,
        is_available=_all_available,
        node_type="ProposalEvidenceFactoryAgent",
    )

    assert writing.selected_model == "local-kimi-k3"
    assert structured.selected_model == "local-glm-5"


def test_maximum_accuracy_priority_prefers_hosted_over_free_local_tie():
    # local-kimi-k3/local-glm-5 are $0-metered -> would otherwise win every
    # cost tie-break. Under accuracy_priority: maximum, cost should not
    # decide between equal-quality models; a hosted premium-tier model should
    # win instead, since local infra doesn't carry the same reliability
    # guarantees.
    decision = ModelRouter().select(
        method_name="complete",
        kwargs={
            "system": "Draft a Horizon Europe proposal.",
            "user": "x" * 40_000,
            "max_tokens": 8_000,
        },
        input_tokens=15_000,
        allowed_models=DEFAULT_LLM_MODELS,
        is_available=_all_available,
        node_type="TransformAgent",
        policy={"accuracy_priority": "maximum"},
    )

    assert decision.selected_model not in {"local-kimi-k3", "local-glm-5"}
    assert decision.selected_model == "gpt-5.6-sol"


class RecordingGateway:
    def __init__(self):
        self.calls: list[str] = []

    async def complete(self, *, model: str, **_kwargs):
        self.calls.append(model)
        return LLMResponse(
            text="ok",
            model=model,
            input_tokens=10,
            output_tokens=5,
        )

    async def complete_structured(
        self,
        *,
        model: str,
        response_model,
        **_kwargs,
    ):
        self.calls.append(model)
        return SimpleNamespace(
            parsed=response_model(answer="ok"),
            model=model,
            input_tokens=10,
            output_tokens=5,
        )

    async def chat_with_tools(self, *, model: str, **_kwargs):
        self.calls.append(model)
        return SimpleNamespace(
            text="ok",
            model=model,
            input_tokens=10,
            output_tokens=5,
        )


class ModelAccessError(RuntimeError):
    status_code = 403


class AccessAwareRecordingGateway(RecordingGateway):
    def __init__(self, denied: set[str] | None = None):
        super().__init__()
        self.denied = denied or set()
        self.probes: list[str] = []

    async def probe_model_access(self, model: str):
        self.probes.append(model)
        if model in self.denied:
            raise ModelAccessError("model is not available to this project")
        return model


class RuntimeModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"error": {"code": code}} if code else None


class RuntimeFailingRecordingGateway(RecordingGateway):
    def __init__(self, failures: dict[str, BaseException] | None = None):
        super().__init__()
        self.failures = failures or {}

    def _raise_failure(self, model: str) -> None:
        failure = self.failures.get(model)
        if failure is not None:
            raise failure

    async def complete(self, *, model: str, **_kwargs):
        self.calls.append(model)
        self._raise_failure(model)
        return LLMResponse(
            text="ok",
            model=model,
            input_tokens=10,
            output_tokens=5,
        )

    async def complete_structured(
        self,
        *,
        model: str,
        response_model,
        **_kwargs,
    ):
        self.calls.append(model)
        self._raise_failure(model)
        return SimpleNamespace(
            parsed=response_model(answer="ok"),
            model=model,
            input_tokens=10,
            output_tokens=5,
        )

    async def chat_with_tools(self, *, model: str, **_kwargs):
        self.calls.append(model)
        self._raise_failure(model)
        return SimpleNamespace(
            text="ok",
            model=model,
            input_tokens=10,
            output_tokens=5,
        )


class StructuredAnswer(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_registry_auto_selection_is_visible_in_events(monkeypatch):
    anthropic = RecordingGateway()
    openai = RecordingGateway()
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.AnthropicGateway: anthropic,
            registry.OpenAIGateway: openai,
        },
    )
    # This test verifies registry routing/event behavior with injected cloud
    # gateways. Deployment environment variables must not make it call a real
    # local endpoint.
    monkeypatch.setattr(registry, "_is_model_available", _cloud_available)
    bus = RunEventBus()
    queue = await bus.subscribe("run-1", "user-1")
    gateway = RegistryLLMGateway().with_context(
        run_id="run-1",
        session_id="user-1",
        node_id="extract",
        event_bus=bus,
        node_type="TransformAgent",
        allowed_models=DEFAULT_LLM_MODELS,
        routing_policy={"accuracy_priority": "balanced"},
    )

    response = await gateway.complete(
        model=AUTO_MODEL,
        system="Extract the title.",
        user="A short document",
        max_tokens=300,
    )
    event = await queue.get()

    assert response.text == "ok"
    assert event.type == "model_selected"
    assert event.context is not None
    assert event.context["requested_model"] == AUTO_MODEL
    assert event.context["actual_model"] == "gpt-4o-mini"
    assert gateway.selection_history[-1]["actual_model"] == (
        "gpt-4o-mini"
    )
    assert openai.calls == ["gpt-4o-mini"]
    assert anthropic.calls == []


@pytest.mark.asyncio
async def test_registry_auto_selection_applies_to_structured_calls(monkeypatch):
    anthropic = RecordingGateway()
    openai = RecordingGateway()
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.AnthropicGateway: anthropic,
            registry.OpenAIGateway: openai,
        },
    )
    monkeypatch.setattr(registry, "_is_model_available", _cloud_available)
    bus = RunEventBus()
    queue = await bus.subscribe("run-structured", "user-1")
    gateway = RegistryLLMGateway().with_context(
        run_id="run-structured",
        session_id="user-1",
        node_id="verify",
        event_bus=bus,
        node_type="ProposalEvidenceFactoryAgent",
        allowed_models=DEFAULT_LLM_MODELS,
        routing_policy={"accuracy_priority": "maximum"},
    )

    result = await gateway.complete_structured(
        model=AUTO_MODEL,
        system="Verify scientific evidence and return the schema.",
        user="x" * 40_000,
        response_model=StructuredAnswer,
        max_tokens=8_000,
    )
    event = await queue.get()

    assert result.answer == "ok"
    assert event.type == "model_selected"
    assert event.context is not None
    assert event.context["task_kind"] == "structured"
    assert event.context["actual_model"] == "gpt-5.6-sol"
    assert openai.calls == ["gpt-5.6-sol"]
    assert anthropic.calls == []


def test_auto_runtime_chain_preserves_order_and_allowed_models():
    gateway = RegistryLLMGateway().with_context(
        run_id="run-chain",
        session_id="user-1",
        node_id="verify",
        allowed_models=["gpt-5.6-sol", "claude-opus-5", "gpt-5"],
    )

    chain = gateway._models_for_call(
        "gpt-5.6-sol",
        candidate_models=(
            "gpt-5.6-sol",
            "disallowed-model",
            "claude-opus-5",
            "gpt-5",
        ),
    )

    assert chain == ["gpt-5.6-sol", "claude-opus-5", "gpt-5"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeModelError("forbidden", status_code=403),
        RuntimeModelError("missing", status_code=404),
        RuntimeModelError(
            "provider rejected the requested model",
            status_code=400,
            code="model_not_found",
        ),
    ],
    ids=["http-403", "http-404", "model-not-found"],
)
async def test_auto_runtime_model_unavailable_advances_without_retry(
    monkeypatch,
    failure,
):
    openai = RuntimeFailingRecordingGateway(
        failures={"gpt-5.6-sol": failure},
    )
    anthropic = RuntimeFailingRecordingGateway()
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.OpenAIGateway: openai,
            registry.AnthropicGateway: anthropic,
        },
    )
    monkeypatch.setattr(registry, "_is_model_available", _cloud_available)

    async def unexpected_sleep(_delay):
        raise AssertionError("an unavailable model must not be retried")

    gateway = RegistryLLMGateway(
        retry_policy=RetryPolicy(max_attempts=3),
        sleep=unexpected_sleep,
    ).with_context(
        run_id="run-runtime-fallback",
        session_id="user-1",
        node_id="partb_metadata",
        event_bus=RunEventBus(),
        node_type="TransformAgent",
        allowed_models=["gpt-5.6-sol", "claude-opus-5", "gpt-5"],
        routing_policy={"accuracy_priority": "maximum"},
    )

    result = await gateway.complete_structured(
        model=AUTO_MODEL,
        system="Extract renderer metadata and return the schema.",
        user="x" * 76_000,
        response_model=StructuredAnswer,
        max_tokens=4_000,
    )

    assert result.answer == "ok"
    assert openai.calls == ["gpt-5.6-sol"]
    assert anthropic.calls == ["claude-opus-5"]
    assert gateway.selection_history[-1]["candidate_models"] == [
        "gpt-5.6-sol",
        "claude-opus-5",
        "gpt-5",
    ]
    assert gateway.selection_history[-1]["actual_model"] == "claude-opus-5"
    assert gateway.selection_history[-1]["fallback"] is True

    second_result = await gateway.complete_structured(
        model=AUTO_MODEL,
        system="Extract renderer metadata and return the schema.",
        user="x" * 76_000,
        response_model=StructuredAnswer,
        max_tokens=4_000,
    )

    assert second_result.answer == "ok"
    assert openai.calls == ["gpt-5.6-sol"]
    assert anthropic.calls == ["claude-opus-5", "claude-opus-5"]
    assert gateway.selection_history[-1]["selected_model"] == "claude-opus-5"
    assert gateway.selection_history[-1]["fallback"] is False


@pytest.mark.asyncio
async def test_auto_runtime_chain_can_skip_multiple_unavailable_models(
    monkeypatch,
):
    openai = RuntimeFailingRecordingGateway(
        failures={
            "gpt-5.6-sol": RuntimeModelError(
                "forbidden",
                status_code=403,
            ),
        },
    )
    anthropic = RuntimeFailingRecordingGateway(
        failures={
            "claude-opus-5": RuntimeModelError(
                "missing",
                status_code=404,
            ),
        },
    )
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.OpenAIGateway: openai,
            registry.AnthropicGateway: anthropic,
        },
    )
    monkeypatch.setattr(registry, "_is_model_available", _cloud_available)
    gateway = RegistryLLMGateway(
        retry_policy=RetryPolicy(max_attempts=3),
    ).with_context(
        run_id="run-multiple-fallbacks",
        session_id="user-1",
        node_id="partb_metadata",
        event_bus=RunEventBus(),
        node_type="TransformAgent",
        allowed_models=["gpt-5.6-sol", "claude-opus-5", "gpt-5"],
        routing_policy={"accuracy_priority": "maximum"},
    )

    result = await gateway.complete_structured(
        model=AUTO_MODEL,
        system="Extract renderer metadata and return the schema.",
        user="x" * 76_000,
        response_model=StructuredAnswer,
        max_tokens=4_000,
    )

    assert result.answer == "ok"
    assert openai.calls == ["gpt-5.6-sol", "gpt-5"]
    assert anthropic.calls == ["claude-opus-5"]
    assert gateway.selection_history[-1]["actual_model"] == "gpt-5"


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["complete", "chat_with_tools"])
async def test_auto_runtime_fallback_is_wired_for_other_call_types(
    monkeypatch,
    method_name,
):
    openai = RuntimeFailingRecordingGateway(
        failures={
            "gpt-5.6-sol": RuntimeModelError(
                "forbidden",
                status_code=403,
            ),
        },
    )
    anthropic = RuntimeFailingRecordingGateway(
        failures={
            "claude-opus-5": RuntimeModelError(
                "forbidden",
                status_code=403,
            ),
        },
    )
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.OpenAIGateway: openai,
            registry.AnthropicGateway: anthropic,
        },
    )
    monkeypatch.setattr(registry, "_is_model_available", _cloud_available)
    node_type = (
        "ProposalSectionDraftAgent"
        if method_name == "complete"
        else "MCPAgent"
    )
    gateway = RegistryLLMGateway(
        retry_policy=RetryPolicy(max_attempts=3),
    ).with_context(
        run_id=f"run-{method_name}",
        session_id="user-1",
        node_id="proposal-node",
        event_bus=RunEventBus(),
        node_type=node_type,
        allowed_models=["gpt-5.6-sol", "claude-opus-5", "gpt-5"],
        routing_policy={"accuracy_priority": "maximum"},
    )

    if method_name == "complete":
        response = await gateway.complete(
            model=AUTO_MODEL,
            system="Draft the proposal section.",
            user="x" * 76_000,
            max_tokens=4_000,
        )
    else:
        response = await gateway.chat_with_tools(
            model=AUTO_MODEL,
            system="Research the proposal evidence.",
            messages=[{"role": "user", "content": "x" * 76_000}],
            tools=[],
            max_tokens=4_000,
        )

    assert response.text == "ok"
    assert gateway.selection_history[-1]["fallback"] is True
    assert gateway.selection_history[-1]["actual_model"] == "gpt-5"


@pytest.mark.asyncio
async def test_zero_token_probe_excludes_inaccessible_auto_model(monkeypatch):
    openai = AccessAwareRecordingGateway(denied={"gpt-5.6-sol"})
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {registry.OpenAIGateway: openai},
    )
    gateway = RegistryLLMGateway()

    access = await gateway.probe_model_access(
        {"gpt-5.6-sol", "gpt-5"},
    )

    assert access["gpt-5.6-sol"].available is False
    assert access["gpt-5.6-sol"].status_code == 403
    assert access["gpt-5"].available is True
    assert openai.calls == []

    bound = gateway.with_context(
        run_id="run-access",
        session_id="user-1",
        node_id="verify",
        event_bus=RunEventBus(),
        node_type="ProposalEvidenceFactoryAgent",
        allowed_models=["gpt-5.6-sol", "gpt-5"],
        routing_policy={"accuracy_priority": "maximum"},
    )
    result = await bound.complete_structured(
        model=AUTO_MODEL,
        system="Verify scientific evidence and return the schema.",
        user="x" * 40_000,
        response_model=StructuredAnswer,
        max_tokens=8_000,
    )

    assert result.answer == "ok"
    assert openai.calls == ["gpt-5"]
    assert bound.selection_history[-1]["selected_model"] == "gpt-5"


@pytest.mark.asyncio
async def test_registry_auto_selection_applies_to_tool_calls(monkeypatch):
    anthropic = RecordingGateway()
    openai = RecordingGateway()
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.AnthropicGateway: anthropic,
            registry.OpenAIGateway: openai,
        },
    )
    monkeypatch.setattr(registry, "_is_model_available", _cloud_available)
    bus = RunEventBus()
    queue = await bus.subscribe("run-tools", "user-1")
    gateway = RegistryLLMGateway().with_context(
        run_id="run-tools",
        session_id="user-1",
        node_id="research",
        event_bus=bus,
        node_type="MCPAgent",
        allowed_models=DEFAULT_LLM_MODELS,
        routing_policy={"accuracy_priority": "maximum"},
    )

    result = await gateway.chat_with_tools(
        model=AUTO_MODEL,
        system="Use research tools and verify the result.",
        messages=[{"role": "user", "content": "x" * 20_000}],
        tools=[
            {"name": "one"},
            {"name": "two"},
            {"name": "three"},
            {"name": "four"},
        ],
        max_tokens=4_000,
    )
    event = await queue.get()

    assert result.text == "ok"
    assert event.type == "model_selected"
    assert event.context is not None
    assert event.context["task_kind"] == "tool_use"
    assert event.context["actual_model"] == "gpt-5.6-sol"
    assert openai.calls == ["gpt-5.6-sol"]
    assert anthropic.calls == []


@pytest.mark.asyncio
async def test_workflow_state_keeps_model_selection_after_completion(
    monkeypatch,
):
    anthropic = RecordingGateway()
    openai = RecordingGateway()
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.AnthropicGateway: anthropic,
            registry.OpenAIGateway: openai,
        },
    )
    monkeypatch.setattr(registry, "_is_model_available", _cloud_available)
    spec = load_workflow_from_string(
        """
name: auto-routing-state
nodes:
  - id: extract
    type: TransformAgent
    selected_model: auto
    model_routing:
      accuracy_priority: balanced
    config:
      model: claude-opus-5
      prompt_template: Extract the title from this short document.
      max_tokens: 300
entry: extract
exit: extract
"""
    )

    result = await run_workflow(
        spec,
        inputs={},
        services={"llm": RegistryLLMGateway()},
        run_id="run-state-1",
    )

    selections = result["state"]["model_selections"]
    assert selections[-1]["requested_model"] == AUTO_MODEL
    assert selections[-1]["actual_model"] == "gpt-4o-mini"
    assert selections[-1]["reason"]