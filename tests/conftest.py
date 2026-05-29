"""Shared test fixtures: stub LLM and stub retriever for node tests.

Phase 5 nodes receive their LLM gateway and retriever through dependency
injection. In tests we inject these stubs instead of real clients."""
from __future__ import annotations
from typing import Any
import pytest


class StubLLM:
    """A scripted LLM. Pre-load it with the responses you want it to return
    in order. Records every call for assertion."""

    def __init__(self, responses: list[str] | None = None):
        self.responses: list[str] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, response: str) -> None:
        self.responses.append(response)

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        system: str | None = None,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> str:
        self.calls.append({
            "model": model,
            "messages": messages,
            "system": system,
            "temperature": temperature,
        })
        if not self.responses:
            raise RuntimeError(
                f"StubLLM exhausted after {len(self.calls)} call(s). "
                f"Last messages: {messages!r}"
            )
        return self.responses.pop(0)


@pytest.fixture
def stub_llm() -> StubLLM:
    return StubLLM()


@pytest.fixture
def services_with_llm(stub_llm: StubLLM) -> dict[str, Any]:
    """The services dict the compiler injects into nodes."""
    return {"llm": stub_llm}