from __future__ import annotations
from typing import Any, Type
import pytest
from pydantic import BaseModel

from app.llm.base import LLMResponse


class StubLLM:
    """A scripted LLM. Pre-load responses; records every call for assertion."""

    def __init__(self, responses: list[str] | None = None):
        self.responses: list[str] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, response: str) -> None:
        self.responses.append(response)

    def _next(self) -> str:
        if not self.responses:
            raise RuntimeError(
                f"StubLLM exhausted after {len(self.calls)} call(s). "
                f"Last call: {self.calls[-1] if self.calls else None!r}"
            )
        return self.responses.pop(0)

    async def complete(
        self, *, model: str, system: str, user: str,
        temperature: float = 0.0, max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append({"method": "complete", "model": model, "system": system, "user": user})
        return LLMResponse(text=self._next(), model=model, input_tokens=0, output_tokens=0)

    async def complete_structured(
        self, *, model: str, system: str, user: str,
        response_model: Type[BaseModel],
        temperature: float = 0.0, max_tokens: int = 1024,
    ):
        self.calls.append({"method": "complete_structured", "model": model, "response_model": response_model.__name__})
        # The queued response is a JSON string; parse it into the model.
        return response_model.model_validate_json(self._next())

    # Kept for any node not yet migrated off .chat()
    async def chat(self, *, model: str, messages: list[dict],
                   system: str | None = None, temperature: float = 0.2, **kwargs: Any) -> str:
        self.calls.append({"method": "chat", "model": model, "messages": messages, "system": system})
        return self._next()


@pytest.fixture
def stub_llm() -> StubLLM:
    return StubLLM()


@pytest.fixture
def services_with_llm(stub_llm: StubLLM) -> dict[str, Any]:
    return {"llm": stub_llm}