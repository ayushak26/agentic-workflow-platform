from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.llm.local_openai_gw import (
    LocalModelProfile,
    LocalOpenAICompatibleGateway,
    normalize_openai_base_url,
)
from app.runtime.preflight import preflight_workflow_yaml


class Verdict(BaseModel):
    supported: bool
    reason: str


def _response(
    *,
    content: str | None,
    tool_calls=None,
    reasoning_content: str | None = None,
):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
        reasoning_content=reasoning_content,
        model_extra={},
    )
    return SimpleNamespace(
        model="served-model",
        choices=[
            SimpleNamespace(
                message=message,
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
    )


def _client(*responses):
    client = SimpleNamespace()
    client.chat = SimpleNamespace()
    client.chat.completions = SimpleNamespace(
        create=AsyncMock(side_effect=list(responses))
    )
    client.models = SimpleNamespace(
        list=AsyncMock(
            return_value=SimpleNamespace(
                data=[SimpleNamespace(id="served-model")]
            )
        )
    )
    return client


def _profile(**overrides):
    values = {
        "alias": "local-kimi-k3",
        "provider": "moonshot-local",
        "enabled": True,
        "base_url": "http://inference.internal:8000",
        "api_key": "",
        "served_model": "served-model",
        "reasoning_effort": "max",
    }
    values.update(overrides)
    return LocalModelProfile(**values)


def test_local_base_url_is_validated_and_normalized():
    assert (
        normalize_openai_base_url("http://inference.internal:8000")
        == "http://inference.internal:8000/v1"
    )
    with pytest.raises(ValueError, match="credentials"):
        normalize_openai_base_url("http://user:secret@localhost:8000/v1")


@pytest.mark.asyncio
async def test_local_completion_uses_served_model_and_reasoning_effort():
    client = _client(_response(content="answer"))
    gateway = LocalOpenAICompatibleGateway(_profile(), client=client)

    response = await gateway.complete(
        model="local-kimi-k3",
        system="system",
        user="question",
        max_tokens=321,
    )

    assert response.text == "answer"
    assert response.model == "local-kimi-k3"
    assert response.input_tokens == 12
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "served-model"
    assert kwargs["max_tokens"] == 321
    assert kwargs["extra_body"]["reasoning_effort"] == "max"


@pytest.mark.asyncio
async def test_local_structured_output_is_schema_constrained_and_validated():
    client = _client(
        _response(content='{"supported":true,"reason":"page 4 supports it"}')
    )
    gateway = LocalOpenAICompatibleGateway(_profile(), client=client)

    result = await gateway.complete_structured(
        model="local-kimi-k3",
        system="system",
        user="verify",
        response_model=Verdict,
    )

    assert result.parsed.supported is True
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_tool_chat_preserves_kimi_reasoning_history():
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="search",
            arguments='{"query":"biomass"}',
        ),
    )
    client = _client(
        _response(
            content=None,
            tool_calls=[tool_call],
            reasoning_content="I should search the verified corpus.",
        )
    )
    gateway = LocalOpenAICompatibleGateway(_profile(), client=client)

    response = await gateway.chat_with_tools(
        model="local-kimi-k3",
        system="system",
        messages=[
            {"role": "user", "content": "Find evidence."},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Earlier preserved thought.",
                "tool_calls": [],
            },
        ],
        tools=[
            {
                "name": "search",
                "description": "Search",
                "input_schema": {"type": "object"},
            }
        ],
    )

    assert response.reasoning_content == "I should search the verified corpus."
    assert response.tool_calls[0].arguments == {"query": "biomass"}
    sent = client.chat.completions.create.await_args.kwargs["messages"]
    assert sent[2]["reasoning_content"] == "Earlier preserved thought."


@pytest.mark.asyncio
async def test_glm_thinking_flag_and_model_probe():
    client = _client(_response(content="answer"))
    gateway = LocalOpenAICompatibleGateway(
        _profile(
            alias="local-glm-5",
            provider="zai-local",
            enable_thinking=False,
        ),
        client=client,
    )

    await gateway.complete(
        model="local-glm-5",
        system="system",
        user="question",
    )
    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["extra_body"]["enable_thinking"] is False
    assert await gateway.probe() is True


def test_local_model_workflow_declares_endpoint_as_required_service():
    report = preflight_workflow_yaml(
        """
name: local-test
nodes:
  - id: draft
    type: TransformAgent
    config:
      model: local-kimi-k3
      prompt_template: Draft this.
edges: []
entry: draft
exit: draft
"""
    )

    assert report.valid is True
    assert "llm:local-kimi-k3" in report.required_services
