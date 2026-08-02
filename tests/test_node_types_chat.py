from __future__ import annotations

from types import SimpleNamespace

import app.nodes  # noqa: F401
import pytest
from pydantic import BaseModel

from fastapi import HTTPException

from app.api.node_types_chat import (
    NODE_TYPE_CHAT_MODEL,
    PROMPT_DRAFTING_MODEL,
    AskAboutNodeTypesRequest,
    DraftPromptRequest,
    _build_node_type_catalog,
    ask_about_node_types,
    draft_prompt,
)
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.security.dependencies import CurrentUser
from app.security.rbac import Role

USER = CurrentUser(username="user@example.com", role=Role.CONSULTANT, session_id=None)


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []

    def with_context(self, **_kwargs):
        return self

    async def complete(self, *, model, system, user, temperature=0.0, **_kwargs):
        self.calls.append({"model": model, "system": system, "user": user})
        return SimpleNamespace(text="mock explanation", model=model)


def _request(llm):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services={"llm": llm})))


@pytest.mark.asyncio
async def test_ask_about_node_types_uses_the_configured_model_and_grounds_in_the_live_catalog():
    llm = FakeLLM()

    result = await ask_about_node_types(
        AskAboutNodeTypesRequest(question="What is TransformAgent for?"),
        _request(llm),
        USER,
    )

    assert result["answer"] == "mock explanation"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["model"] == NODE_TYPE_CHAT_MODEL == "gpt-5.6-terra"
    assert "TransformAgent" in call["user"]
    assert "What is TransformAgent for?" in call["user"]
    # The system prompt is the guardrail against hallucinating node types
    # that don't exist — assert the instruction is actually present.
    assert "does not exist in this platform" in call["system"]


def test_catalog_never_lists_a_type_name_outside_the_real_registry():
    catalog = _build_node_type_catalog()
    real_type_names = set(NodeRegistry._registry.keys())

    for line in catalog.splitlines():
        # Each catalog line looks like "- <type_name> (category: ...): ...".
        listed_name = line.removeprefix("- ").split(" (category:", 1)[0]
        assert listed_name in real_type_names


def test_catalog_reflects_a_newly_registered_node_type_without_any_other_change():
    class _DummyConfig(BaseModel):
        pass

    class _DummyOutput(BaseModel):
        pass

    dummy_type_name = "TestOnlyDummyNodeType"
    assert dummy_type_name not in NodeRegistry._registry

    try:
        @NodeRegistry.register
        class _DummyNode(NodeType):
            type_name = dummy_type_name
            description = "A node type registered only for this test."
            input_schema = _DummyConfig
            output_schema = _DummyOutput
            config_schema = _DummyConfig

            async def run(self, state, resolved_config):
                return {}

        catalog = _build_node_type_catalog()
        assert dummy_type_name in catalog
        assert "A node type registered only for this test." in catalog
    finally:
        NodeRegistry._registry.pop(dummy_type_name, None)

    # Torn down — the dummy type must not leak into other tests.
    assert dummy_type_name not in _build_node_type_catalog()


@pytest.mark.asyncio
async def test_draft_prompt_uses_luna_and_grounds_in_the_node_types_own_description():
    llm = FakeLLM()

    result = await draft_prompt(
        DraftPromptRequest(
            type_name="TransformAgent",
            field_name="prompt_template",
            instruction="Draft a prompt that summarizes competitor pricing pages.",
        ),
        _request(llm),
        USER,
    )

    assert result["answer"] == "mock explanation"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["model"] == PROMPT_DRAFTING_MODEL == "gpt-5.6-luna"
    assert "TransformAgent" in call["user"]
    assert "prompt_template" in call["user"]
    assert "summarizes competitor pricing pages" in call["user"]


@pytest.mark.asyncio
async def test_draft_prompt_404s_for_a_node_type_outside_the_registry():
    llm = FakeLLM()

    with pytest.raises(HTTPException) as exc_info:
        await draft_prompt(
            DraftPromptRequest(
                type_name="NotARealNodeType",
                field_name="prompt_template",
                instruction="Draft something.",
            ),
            _request(llm),
            USER,
        )
    assert exc_info.value.status_code == 404
    assert len(llm.calls) == 0
