from __future__ import annotations

import pytest

import app.llm.registry as registry
from app.llm.base import LLMResponse
from app.llm.model_catalog import AUTO_MODEL, DEFAULT_LLM_MODELS
from app.llm.model_router import ModelRouter, ModelRoutingError
from app.llm.registry import RegistryLLMGateway
from app.runtime.executor import run_workflow
from app.runtime.events import RunEventBus
from app.runtime.loader import load_workflow_from_string


def _all_available(_model: str) -> bool:
    return True


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
        is_available=_all_available,
        node_type="TransformAgent",
    )

    assert decision.mode == "auto"
    assert decision.complexity == "simple"
    assert decision.task_kind == "extraction"
    assert decision.selected_model == "claude-haiku-4-5"


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
        is_available=_all_available,
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
        is_available=_all_available,
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
            is_available=_all_available,
            policy={"max_estimated_cost_usd": 0.000001},
        )


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
    bus = RunEventBus()
    queue = await bus.subscribe("run-1")
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
    assert event.context["actual_model"] == "claude-haiku-4-5"
    assert gateway.selection_history[-1]["actual_model"] == (
        "claude-haiku-4-5"
    )
    assert anthropic.calls == ["claude-haiku-4-5"]
    assert openai.calls == []


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
    assert selections[-1]["actual_model"] == "claude-haiku-4-5"
    assert selections[-1]["reason"]
