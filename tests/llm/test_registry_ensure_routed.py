"""Characterization tests for RegistryLLMGateway.ensure_routed().

RAG Agent "Best possible LLM (Auto)" was broken for any direct (non-workflow)
caller of RAGService.query() -- e.g. Knowledge Studio's "Test query" button --
because RegistryLLMGateway._complete_impl only takes the deterministic
ModelRouter routing path when event_bus or allowed_models is bound (see the
fast-path check characterized in test_registry_gateway_characterization.py).
A workflow node gets that binding from the compiler; a direct service caller
building its own unbound gateway does not, so "auto" silently degraded to a
fixed first-available-provider pick with no task-aware selection.

ensure_routed() closes that gap: called immediately before a completion whose
model may be "auto", it upgrades an unbound gateway to a routing-bound clone,
and leaves an already-bound gateway (a real workflow node) untouched.

No real network calls are made -- every concrete provider gateway is a fake,
matching the pattern in test_registry_gateway_characterization.py.
"""
from __future__ import annotations

import pytest

import app.llm.registry as registry
from app.llm.base import LLMResponse
from app.llm.registry import RegistryLLMGateway


class RecordingGateway:
    def __init__(self, response_text: str = "ok"):
        self.calls: list[tuple[str, dict]] = []
        self.response_text = response_text

    async def complete(self, *, model, **kwargs):
        self.calls.append((model, kwargs))
        return LLMResponse(text=self.response_text, model=model, input_tokens=10, output_tokens=5)


@pytest.fixture(autouse=True)
def _fake_providers(monkeypatch):
    # Every model in the catalog resolves to one shared fake gateway instance,
    # and every model is treated as configured/available -- isolates this
    # test from real API keys / ModelRouter's own scoring logic (covered
    # separately in test_model_router.py).
    fake = RecordingGateway()
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.AnthropicGateway: fake,
            registry.OpenAIGateway: fake,
        },
    )
    monkeypatch.setattr(registry, "_is_model_available", lambda model: True)
    return fake


def test_ensure_routed_upgrades_an_unbound_gateway():
    gateway = RegistryLLMGateway()

    routed = gateway.ensure_routed(node_type="RAGAgent")

    assert routed is not gateway
    assert routed._allowed_models is not None
    assert routed._node_type == "RAGAgent"


def test_ensure_routed_leaves_an_already_bound_gateway_unchanged():
    """A real workflow node's gateway is already routing-bound by the
    compiler (allowed_models set) -- ensure_routed must never narrow that
    node's own allowed_models boundary by rebinding it."""
    gateway = RegistryLLMGateway()
    node_bound = gateway.with_context(
        run_id="run-1", session_id="sess-1", node_id="node-1",
        allowed_models=["claude-opus-5"],
    )

    routed = node_bound.ensure_routed(node_type="RAGAgent")

    assert routed is node_bound
    assert routed._allowed_models == ["claude-opus-5"]


@pytest.mark.asyncio
async def test_unbound_auto_call_takes_the_fast_path_with_no_selection_history():
    """Baseline: without ensure_routed, "auto" on an unbound gateway never
    populates selection_history -- confirms the bug this fix addresses."""
    gateway = RegistryLLMGateway()

    await gateway.complete(model="auto", system="s", user="u")

    assert gateway.selection_history == []


@pytest.mark.asyncio
async def test_ensure_routed_then_auto_call_populates_selection_history():
    """The fix: after ensure_routed(), the same "auto" call goes through
    ModelRouter.select() and records a selection -- the deterministic,
    task-aware resolution workflow nodes already get."""
    gateway = RegistryLLMGateway().ensure_routed(node_type="RAGAgent")

    resp = await gateway.complete(model="auto", system="s", user="u")

    assert resp.model != "auto"
    assert len(gateway.selection_history) == 1
    assert gateway.selection_history[0]["requested_model"] == "auto"


@pytest.mark.asyncio
async def test_ensure_routed_does_not_affect_an_explicit_model_call():
    """Gating ensure_routed's use to model == AUTO_MODEL at the call site
    (as RAGService does) means an explicit model keeps using the exact same
    fast path it uses today -- no regression for manual model selection."""
    gateway = RegistryLLMGateway()

    resp = await gateway.complete(model="claude-opus-5", system="s", user="u")

    assert resp.model == "claude-opus-5"
    assert gateway.selection_history == []
