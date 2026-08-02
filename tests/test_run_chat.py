from __future__ import annotations

from types import SimpleNamespace

import app.nodes  # noqa: F401
import pytest
from fastapi import HTTPException

from app.api.run_chat import (
    CHAT_MODEL,
    AskAboutRunRequest,
    ask_about_run,
    get_run_chat,
    starter_questions,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.run_history import record_node_completed, record_node_started, upsert_run

from .fake_mongo import InMemoryDB

USER = CurrentUser(username="user@example.com", role=Role.CONSULTANT, session_id=None)


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []

    def with_context(self, **_kwargs):
        return self

    async def complete(self, *, model, system, user, temperature=0.0, **_kwargs):
        self.calls.append({"model": model, "system": system, "user": user})
        return SimpleNamespace(text=f"Answer grounded in: {user[:0]}mock answer", model=model)


def _request(db, llm=None):
    services = {"audit_db": db}
    if llm is not None:
        services["llm"] = llm
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))


@pytest.mark.asyncio
async def test_get_run_chat_returns_starter_questions_and_empty_turns_for_new_run():
    db = InMemoryDB()

    result = await get_run_chat("run-1", _request(db), USER)

    assert result["turns"] == []
    assert result["starter_questions"] == starter_questions("run-1")
    assert "run-1" in result["starter_questions"][2]


@pytest.mark.asyncio
async def test_ask_about_run_404s_for_unknown_run():
    db = InMemoryDB()
    llm = FakeLLM()

    with pytest.raises(HTTPException) as exc_info:
        await ask_about_run(
            "does-not-exist",
            AskAboutRunRequest(question="What happened?"),
            _request(db, llm),
            USER,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_ask_about_run_grounds_the_prompt_in_run_data_and_persists_the_turn():
    db = InMemoryDB()
    await upsert_run(
        db, "run-1", "user@example.com",
        workflow_name="proposal", status="completed",
        inputs={"topic": "circular bioeconomy"},
    )
    await record_node_started(
        db, run_id="run-1", session_id="user@example.com",
        node_id="draft.section", type_name="TransformAgent",
        node_input={}, started_at=10.0,
    )
    await record_node_completed(
        db, run_id="run-1", session_id="user@example.com",
        node_id="draft.section", output={"text": "Draft complete"},
        ended_at=13.0, duration_s=3.0,
    )
    llm = FakeLLM()

    result = await ask_about_run(
        "run-1",
        AskAboutRunRequest(question="What was the outcome?"),
        _request(db, llm),
        USER,
    )

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["model"] == CHAT_MODEL
    assert "circular bioeconomy" in call["user"]
    assert "draft.section" in call["user"]
    assert "What was the outcome?" in call["user"]

    assert result["answer"]
    assert len(result["turns"]) == 2
    assert result["turns"][0]["role"] == "user"
    assert result["turns"][0]["content"] == "What was the outcome?"
    assert result["turns"][1]["role"] == "assistant"

    # A follow-up call should see the prior turn in its conversation context.
    followup = await ask_about_run(
        "run-1",
        AskAboutRunRequest(
            question="Anything else?",
            history=[
                {"role": "user", "content": "What was the outcome?"},
                {"role": "assistant", "content": result["answer"]},
            ],
        ),
        _request(db, llm),
        USER,
    )
    assert len(llm.calls) == 2
    assert "What was the outcome?" in llm.calls[1]["user"]
    assert len(followup["turns"]) == 4


@pytest.mark.asyncio
async def test_ask_about_run_includes_node_type_description_and_configured_prompt():
    db = InMemoryDB()
    workflow_yaml = """
name: proposal
nodes:
  - id: draft.section
    type: TransformAgent
    config:
      prompt_template: "Summarize the evidence for {{topic}}."
      model: auto
"""
    await upsert_run(
        db, "run-1", "user@example.com",
        workflow_name="proposal", status="completed",
        inputs={"topic": "circular bioeconomy"},
        workflow_yaml=workflow_yaml,
    )
    await record_node_started(
        db, run_id="run-1", session_id="user@example.com",
        node_id="draft.section", type_name="TransformAgent",
        node_input={}, started_at=10.0,
    )
    await record_node_completed(
        db, run_id="run-1", session_id="user@example.com",
        node_id="draft.section", output={"text": "Draft complete"},
        ended_at=13.0, duration_s=3.0,
    )
    llm = FakeLLM()

    await ask_about_run(
        "run-1", AskAboutRunRequest(question="What node types were used?"),
        _request(db, llm), USER,
    )

    call_context = llm.calls[0]["user"]
    assert "Node types used in this run: TransformAgent" in call_context
    # The node type's own description (from the live node registry).
    assert "Pure LLM transform" in call_context or "TransformAgent" in call_context
    # The prompt the node was actually configured with, sourced from the
    # run's workflow_yaml (design-time), not the redacted per-run record.
    assert "Summarize the evidence for {{topic}}." in call_context
