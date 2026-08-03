"""Regression coverage for NodeSpec.data_protection_mode -- a per-node
override of the workflow/platform-wide entity-protection mode.

Motivating case: ScholarlyCandidateDiscoveryAgent, StructuredDatasetRetrieverAgent
(auto_plan_queries), and similar nodes plan external search queries (author
names, organisation names, project names) via an LLM call, then send the
planned query text VERBATIM to an external search API (Semantic Scholar,
Eurostat, etc.) that never touches the LLM gateway. Tokenizing that planning
call hands the search API a useless placeholder instead of the real name it
needs to search for -- but the SAME workflow's concept-note/proposal nodes
(referencing the same confidential partner/organisation names) must stay
fully protected. This can only be solved per-node, not per-workflow.
"""
from __future__ import annotations

import pytest

import app.llm.registry as registry
from app.config import settings
from app.llm.base import LLMResponse
from app.llm.registry import RegistryLLMGateway
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.security.entity_tokenizer import EntityTokenizerService
from tests.security._fake_mongo import FakeAsyncDatabase

TEST_KEY = "f" * 40

WORKFLOW = """
name: per_node_mode_test
entry: protected_node
nodes:
  - id: protected_node
    type: TransformAgent
    config:
      model: claude-opus-5
      prompt_template: "Acme Robotics GmbH is a partner."
  - id: exempt_node
    type: TransformAgent
    data_protection_mode: public
    config:
      model: claude-opus-5
      prompt_template: "Acme Robotics GmbH is a partner."
edges:
  - from: protected_node
    to: exempt_node
exit: exempt_node
"""


class CapturingGateway:
    def __init__(self, reply_text: str = "ok"):
        self.calls: list[dict] = []
        self.reply_text = reply_text

    async def complete(self, *, model, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(
            text=self.reply_text, model=model, input_tokens=1, output_tokens=1
        )


@pytest.fixture()
def vault_key():
    original = settings.entity_vault_master_key
    settings.entity_vault_master_key = TEST_KEY
    yield
    settings.entity_vault_master_key = original


async def test_node_level_override_bypasses_tokenization_for_that_node_only(
    monkeypatch, vault_key
):
    tokenizer = EntityTokenizerService(FakeAsyncDatabase())
    await tokenizer.registry.register(
        session_id="s1", collection_id="default", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    stub = CapturingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: stub})

    services = {
        "llm": RegistryLLMGateway(),
        "entity_tokenizer": tokenizer,
        "entity_protection_mode": "pseudonymised",
    }
    spec = load_workflow_from_string(WORKFLOW)
    await run_workflow(
        spec, {}, session_id="s1", collection_id="default",
        services=services, run_id="per-node-t1",
    )

    assert len(stub.calls) == 2
    protected_call, exempt_call = stub.calls
    assert "Acme Robotics GmbH" not in protected_call["user"]
    assert "[[ENTITY_ORGANISATION_1]]" in protected_call["user"]
    assert "Acme Robotics GmbH" in exempt_call["user"]


async def test_no_override_still_inherits_the_run_wide_default(
    monkeypatch, vault_key
):
    """Confirms the override is opt-in per node -- a node with no
    data_protection_mode set behaves exactly as before this feature."""
    tokenizer = EntityTokenizerService(FakeAsyncDatabase())
    await tokenizer.registry.register(
        session_id="s1", collection_id="default", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    stub = CapturingGateway()
    monkeypatch.setattr(registry, "_INSTANCES", {registry.AnthropicGateway: stub})

    single_node_workflow = """
name: single_node_test
entry: protected_node
nodes:
  - id: protected_node
    type: TransformAgent
    config:
      model: claude-opus-5
      prompt_template: "Acme Robotics GmbH is a partner."
exit: protected_node
"""
    services = {
        "llm": RegistryLLMGateway(),
        "entity_tokenizer": tokenizer,
        "entity_protection_mode": "pseudonymised",
    }
    spec = load_workflow_from_string(single_node_workflow)
    await run_workflow(
        spec, {}, session_id="s1", collection_id="default",
        services=services, run_id="per-node-t2",
    )
    assert "Acme Robotics GmbH" not in stub.calls[0]["user"]


def test_invalid_data_protection_mode_is_rejected_at_load_time():
    from app.runtime.loader import load_workflow_from_string

    bad_workflow = """
name: bad_mode_test
entry: n
nodes:
  - id: n
    type: TransformAgent
    data_protection_mode: not-a-real-mode
    config:
      model: claude-opus-5
      prompt_template: "hello"
exit: n
"""
    with pytest.raises(Exception):
        load_workflow_from_string(bad_workflow)
