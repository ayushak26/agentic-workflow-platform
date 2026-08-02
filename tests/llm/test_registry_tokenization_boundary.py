"""Proves the Phase 1 core guarantee at the actual chokepoint: a confidential
entity registered in the vault never reaches the concrete provider gateway
in plaintext, and the caller (node code) still gets the real value back.
"""
from __future__ import annotations

import pytest

import app.llm.registry as registry
from app.llm.base import LLMResponse
from app.llm.registry import RegistryLLMGateway
from app.config import settings
from app.security.entity_tokenizer import EntityTokenizerService
from tests.security._fake_mongo import FakeAsyncDatabase

TEST_KEY = "d" * 40


@pytest.fixture()
def vault_key():
    original = settings.entity_vault_master_key
    settings.entity_vault_master_key = TEST_KEY
    yield
    settings.entity_vault_master_key = original


@pytest.fixture()
def tokenizer(vault_key) -> EntityTokenizerService:
    return EntityTokenizerService(FakeAsyncDatabase())


class CapturingGateway:
    """Stands in for the concrete provider — records exactly what it received."""

    def __init__(self, reply_text: str = "The plan looks good."):
        self.calls: list[dict] = []
        self.reply_text = reply_text

    async def complete(self, *, model, **kwargs):
        self.calls.append({"model": model, **kwargs})
        return LLMResponse(
            text=self.reply_text, model=model, input_tokens=10, output_tokens=5
        )


def _bind(gateway: RegistryLLMGateway, *, tokenizer, mode="pseudonymised"):
    return gateway.with_context(
        run_id="r1",
        session_id="s1",
        node_id="n1",
        entity_tokenizer=tokenizer,
        collection_id="c1",
        processing_mode=mode,
    )


async def test_registered_entity_never_reaches_the_provider(
    monkeypatch, tokenizer: EntityTokenizerService
):
    await tokenizer.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Hamburg University of Applied Sciences",
    )
    stub = CapturingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: stub})

    gateway = _bind(RegistryLLMGateway(), tokenizer=tokenizer)
    await gateway.complete(
        model="claude-opus-5",
        system="You are a proposal writer.",
        user="Hamburg University of Applied Sciences will run the lab work.",
    )

    assert len(stub.calls) == 1
    sent_user = stub.calls[0]["user"]
    assert "Hamburg University of Applied Sciences" not in sent_user
    assert "[[ENTITY_ORGANISATION_1]]" in sent_user


async def test_response_is_detokenized_back_to_the_real_value(
    monkeypatch, tokenizer: EntityTokenizerService
):
    await tokenizer.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    stub = CapturingGateway(reply_text="[[ENTITY_ORGANISATION_1]] leads WP3.")
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: stub})

    gateway = _bind(RegistryLLMGateway(), tokenizer=tokenizer)
    resp = await gateway.complete(
        model="claude-opus-5", system="s", user="Acme Robotics GmbH is a partner."
    )

    assert resp.text == "Acme Robotics GmbH leads WP3."


async def test_public_mode_bypasses_tokenization_entirely(
    monkeypatch, tokenizer: EntityTokenizerService
):
    await tokenizer.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    stub = CapturingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: stub})

    gateway = _bind(RegistryLLMGateway(), tokenizer=tokenizer, mode="public")
    await gateway.complete(
        model="claude-opus-5", system="s", user="Acme Robotics GmbH is a partner."
    )

    assert stub.calls[0]["user"] == "Acme Robotics GmbH is a partner."


async def test_no_context_construction_skips_tokenization_unchanged(monkeypatch):
    """A raw RegistryLLMGateway() (scripts/tests, no with_context()) must
    behave exactly as before Phase 1 — this is the documented bypass."""
    stub = CapturingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: stub})

    gateway = RegistryLLMGateway()
    resp = await gateway.complete(
        model="claude-opus-5", system="s", user="Acme Robotics GmbH is a partner."
    )

    assert stub.calls[0]["user"] == "Acme Robotics GmbH is a partner."
    assert resp.text == "The plan looks good."


async def test_structured_response_fields_are_detokenized(
    monkeypatch, tokenizer: EntityTokenizerService
):
    from pydantic import BaseModel

    from app.llm.openai_gw import StructuredResult

    class Plan(BaseModel):
        summary: str
        partners: list[str]

    await tokenizer.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Beta Systems SA",
    )

    class StructuredStub:
        def __init__(self):
            self.calls = []

        async def complete_structured(self, *, model, response_model, **kwargs):
            self.calls.append(kwargs)
            parsed = response_model(
                summary="[[ENTITY_ORGANISATION_1]] is the lead partner.",
                partners=["[[ENTITY_ORGANISATION_1]]"],
            )
            return StructuredResult(
                parsed=parsed, input_tokens=10, output_tokens=5, model=model
            )

    stub = StructuredStub()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: stub})

    gateway = _bind(RegistryLLMGateway(), tokenizer=tokenizer)
    result = await gateway.complete_structured(
        model="claude-opus-5",
        system="s",
        user="Beta Systems SA joined the consortium.",
        response_model=Plan,
    )

    assert "Beta Systems SA" not in stub.calls[0]["user"]
    assert result.summary == "Beta Systems SA is the lead partner."
    assert result.partners == ["Beta Systems SA"]
