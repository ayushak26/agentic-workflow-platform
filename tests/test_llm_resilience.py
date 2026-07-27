from __future__ import annotations

import pytest

import app.llm.registry as registry
from app.llm.base import LLMResponse
from app.llm.registry import RegistryLLMGateway, RetryPolicy


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class ScriptedGateway:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def complete(self, *, model, **kwargs):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(
            text=outcome,
            model=model,
            input_tokens=10,
            output_tokens=5,
        )


@pytest.mark.asyncio
async def test_transient_claude_failure_retries_then_fails_over_to_gpt(
    monkeypatch,
):
    primary = ScriptedGateway(
        [
            ProviderError("rate limited", 429),
            ProviderError("still limited", 429),
        ]
    )
    fallback = ScriptedGateway(["fallback answer"])
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.AnthropicGateway: primary,
            registry.OpenAIGateway: fallback,
        },
    )
    delays = []

    async def no_sleep(delay):
        delays.append(delay)

    gateway = RegistryLLMGateway(
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0.25,
            max_delay_seconds=1,
            jitter_ratio=0,
        ),
        sleep=no_sleep,
    )
    response = await gateway.complete(
        model="claude-opus-5",
        system="s",
        user="u",
    )

    assert response.text == "fallback answer"
    assert primary.calls == ["claude-opus-5", "claude-opus-5"]
    assert fallback.calls == ["gpt-5.6-sol"]
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_non_retryable_error_does_not_retry_or_fail_over(monkeypatch):
    primary = ScriptedGateway([ProviderError("bad credentials", 401)])
    fallback = ScriptedGateway(["must not run"])
    monkeypatch.setattr(
        registry,
        "_INSTANCES",
        {
            registry.AnthropicGateway: primary,
            registry.OpenAIGateway: fallback,
        },
    )
    gateway = RegistryLLMGateway(
        retry_policy=RetryPolicy(max_attempts=3),
    )

    with pytest.raises(ProviderError, match="bad credentials"):
        await gateway.complete(
            model="claude-sonnet-4-5",
            system="s",
            user="u",
        )

    assert primary.calls == ["claude-sonnet-4-5"]
    assert fallback.calls == []


def test_retry_policy_rejects_invalid_configuration():
    with pytest.raises(ValueError, match="at least 1"):
        RetryPolicy(max_attempts=0)
