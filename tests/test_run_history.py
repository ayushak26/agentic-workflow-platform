from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import app.nodes  # noqa: F401
import pytest
from fastapi import HTTPException, Request

from app.api.runs import RetryRunRequest, retry_failed_run, pending_gate, delete_run_endpoint
from app.api.workflows import _scope
from app.config import settings
from app.runtime.executor import run_workflow
from app.runtime.events import RunEvent, RunEventBus
from app.runtime.loader import load_workflow_from_string
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.run_history import (
    build_node_input,
    cleanup_stale_runs,
    get_retry_checkpoint,
    get_run,
    initialize_run_checkpoint,
    list_runs,
    record_checkpoint_node_completed,
    record_node_completed,
    record_node_reused,
    upsert_run,
    workflow_stats,
)
from tests.fake_mongo import InMemoryDB


class FakeDB:
    def __init__(self):
        self.collections: dict[str, AsyncMock] = {}
        self.command = AsyncMock(return_value={"ok": 1})

    def __getitem__(self, name: str) -> AsyncMock:
        if name not in self.collections:
            collection = AsyncMock()
            collection.find_one.return_value = None
            self.collections[name] = collection
        return self.collections[name]


def test_run_scope_cannot_diverge_from_authenticated_history_scope():
    user = CurrentUser(
        username="ayush@example.com",
        role=Role.CONSULTANT,
        session_id=None,
    )

    assert _scope(user, None) == "ayush@example.com"
    assert _scope(user, "ayush@example.com") == "ayush@example.com"
    with pytest.raises(HTTPException, match="session_id must match"):
        _scope(user, "default")


def test_node_input_redacts_prompts_and_credentials():
    payload = build_node_input(
        {
            "inputs": {
                "question": "What changed?",
                "SYSTEM.run_id": "run-1",
            },
            "node_outputs": {"first": {"answer": "A"}},
        },
        {
            "model": "claude-opus-5",
            "prompt_template": "private proposal prompt",
            "max_tokens": 4096,
            "nested": {"api_key": "secret-key", "query": "public query"},
        },
    )

    assert payload["workflow_inputs"] == {"question": "What changed?"}
    assert payload["upstream_node_ids"] == ["first"]
    assert payload["resolved_config"]["prompt_template"] == "<redacted>"
    assert payload["resolved_config"]["max_tokens"] == 4096
    assert payload["resolved_config"]["nested"]["api_key"] == "<redacted>"
    assert payload["resolved_config"]["nested"]["query"] == "public query"


@pytest.mark.asyncio
async def test_running_upsert_has_no_mongo_operator_conflicts():
    db = FakeDB()

    await upsert_run(
        db,
        "run-1",
        "user@example.com",
        workflow_name="proposal",
        status="running",
        inputs={"topic": "circular bioeconomy"},
        started_at=10.0,
        node_count=3,
        completed_node_count=0,
    )

    _, update = db["run_history"].update_one.await_args.args
    assert update["$set"]["status"] == "running"
    assert update["$set"]["error"] is None
    assert not (set(update["$set"]) & set(update["$setOnInsert"]))


@pytest.mark.asyncio
async def test_conversation_only_run_is_directly_visible_but_globally_isolated():
    db = InMemoryDB()
    session = "user@example.com"
    workflow_name = "Shared workflow"
    await upsert_run(
        db, "global-run", session,
        workflow_name=workflow_name,
        status="completed",
        origin="direct",
        history_visibility="global",
    )
    await upsert_run(
        db, "chat-run", session,
        workflow_name=workflow_name,
        status="failed",
        error="Chat-only failure",
        origin="chat_saved_workflow",
        history_visibility="conversation_only",
        workflow_id="research-helper",
        conversation_id="conversation-1",
        message_id="message-1",
    )

    global_runs = await list_runs(db, session)
    all_runs = await list_runs(db, session, include_conversation_only=True)
    stats = await workflow_stats(db, session, workflow_name)

    assert [run["run_id"] for run in global_runs] == ["global-run"]
    assert {run["run_id"] for run in all_runs} == {"global-run", "chat-run"}
    assert stats["sample_size"] == 1
    assert stats["completed_runs"] == 1
    assert stats["failed_runs"] == 0
    assert stats["last_run_status"] == "completed"
    chat_run = await get_run(db, session, "chat-run")
    assert chat_run is not None
    assert chat_run["origin"] == "chat_saved_workflow"
    assert chat_run["workflow_id"] == "research-helper"
    assert chat_run["conversation_id"] == "conversation-1"
    assert chat_run["message_id"] == "message-1"


@pytest.mark.asyncio
async def test_node_completion_persists_output_incrementally():
    db = FakeDB()

    await record_node_completed(
        db,
        run_id="run-1",
        session_id="user@example.com",
        node_id="draft.section",
        output={"text": "Draft complete"},
        model_selections=[
            {
                "requested_model": "auto",
                "selected_model": "claude-opus-5",
                "actual_model": "claude-opus-5",
            }
        ],
        ended_at=13.0,
        duration_s=3.0,
    )

    _, update = db["run_history"].update_one.await_args.args
    safe_key = "draft\uff0esection"
    assert update["$set"][f"node_runs.{safe_key}.status"] == "completed"
    assert update["$set"][f"outputs.{safe_key}"] == {
        "text": "Draft complete"
    }
    assert update["$set"][f"node_runs.{safe_key}.model_selections"] == [
        {
            "requested_model": "auto",
            "selected_model": "claude-opus-5",
            "actual_model": "claude-opus-5",
        }
    ]
    assert update["$pull"] == {"active_nodes": "draft.section"}


@pytest.mark.asyncio
async def test_runtime_writes_node_start_and_output_to_history():
    db = FakeDB()
    spec = load_workflow_from_string(
        """
name: history_smoke
version: "1.0"
nodes:
  - id: first
    type: Literal
    config:
      value: hello
entry: first
exit: first
"""
    )

    result = await run_workflow(
        spec,
        inputs={"question": "hi"},
        session_id="user@example.com",
        services={"audit_db": db},
        run_id="run-1",
    )

    assert result["status"] == "completed"
    updates = db["run_history"].update_one.await_args_list
    assert any(
        call.args[1].get("$set", {}).get("status") == "running"
        for call in updates
    )
    assert any(
        call.args[1].get("$set", {}).get("outputs.first")
        == {"value": "hello"}
        for call in updates
    )


@pytest.mark.asyncio
async def test_private_checkpoint_keeps_exact_replayable_node_result():
    db = FakeDB()

    await initialize_run_checkpoint(
        db,
        run_id="run-1",
        session_id="user@example.com",
        workflow_yaml="name: example",
        inputs={"topic": "bioeconomy"},
        collection_id="proposal",
    )
    await record_checkpoint_node_completed(
        db,
        run_id="run-1",
        session_id="user@example.com",
        node_id="draft.section",
        output={"raw": "completed draft", "parsed": {}},
        extra_state={"domain_state": {"eu_proposal": {"title": "Draft"}}},
    )

    _, update = db["run_checkpoints"].update_one.await_args.args
    safe_key = "draft\uff0esection"
    saved = update["$set"][f"node_results.{safe_key}"]
    assert saved["node_id"] == "draft.section"
    assert saved["output"]["raw"] == "completed draft"
    assert saved["extra_state"]["domain_state"]["eu_proposal"]["title"] == "Draft"


def _spawn_and_reap_dead_pid() -> int:
    """Return a pid guaranteed not to exist: spawn a trivial subprocess and
    wait for it to exit. Deterministic across platforms, unlike guessing a
    large integer."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


@pytest.mark.asyncio
async def test_stale_running_run_with_dead_owner_process_is_marked_failed():
    db = FakeDB()
    stale_updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.stale_run_after_seconds + 60
    )
    db["run_history"].find_one.return_value = {
        "run_id": "run-stale",
        "session_id": "user@example.com",
        "status": "running",
        "updated_at": stale_updated_at,
        "active_nodes": ["compile_v1"],
        "owner_pid": _spawn_and_reap_dead_pid(),
    }

    run = await get_run(db, "user@example.com", "run-stale")

    assert run is not None
    assert run["status"] == "failed"
    assert "no longer running" in run["error"]
    assert run["active_nodes"] == []

    _, history_update = db["run_history"].update_one.await_args.args
    assert history_update["$set"]["status"] == "failed"
    assert history_update["$set"]["node_runs.compile_v1.status"] == "failed"

    _, checkpoint_update = db["run_checkpoints"].update_one.await_args.args
    assert checkpoint_update["$set"]["status"] == "failed"


@pytest.mark.asyncio
async def test_running_run_with_live_owner_process_is_left_alone():
    """A single long LLM call can leave `updated_at` untouched for far longer
    than stale_run_after_seconds while the owning process is very much
    alive — this must not be auto-failed just because time passed."""
    db = FakeDB()
    stale_updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.stale_run_after_seconds + 3600
    )
    db["run_history"].find_one.return_value = {
        "run_id": "run-still-going",
        "session_id": "user@example.com",
        "status": "running",
        "updated_at": stale_updated_at,
        "active_nodes": ["scientific_synthesis"],
        "owner_pid": os.getpid(),
    }

    run = await get_run(db, "user@example.com", "run-still-going")

    assert run is not None
    assert run["status"] == "running"
    db["run_history"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_recently_updated_running_run_is_left_alone():
    db = FakeDB()
    db["run_history"].find_one.return_value = {
        "run_id": "run-live",
        "session_id": "user@example.com",
        "status": "running",
        "updated_at": datetime.now(timezone.utc),
        "active_nodes": ["compile_v1"],
    }

    run = await get_run(db, "user@example.com", "run-live")

    assert run is not None
    assert run["status"] == "running"
    db["run_history"].update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_checkpoint_restores_results_by_original_node_id():
    db = FakeDB()
    db["run_checkpoints"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "inputs": {"topic": "bioeconomy"},
        "node_results": {
            "draft\uff0esection": {
                "node_id": "draft.section",
                "output": {"raw": "draft", "parsed": {}},
                "extra_state": {},
            }
        },
    }

    checkpoint = await get_retry_checkpoint(
        db,
        "user@example.com",
        "run-1",
    )

    assert checkpoint is not None
    assert checkpoint["reusable_results"] == {
        "draft.section": {
            "output": {"raw": "draft", "parsed": {}},
            "extra_state": {},
        }
    }


@pytest.mark.asyncio
async def test_reused_node_is_counted_and_linked_to_source_run():
    db = FakeDB()

    await record_node_reused(
        db,
        run_id="retry-1",
        session_id="user@example.com",
        node_id="writer",
        type_name="TransformAgent",
        output={"raw": "cached", "parsed": {}},
        source_run_id="failed-1",
        ended_at=12.0,
        duration_s=0.01,
    )

    _, update = db["run_history"].update_one.await_args.args
    assert update["$set"]["node_runs.writer"]["status"] == "reused"
    assert (
        update["$set"]["node_runs.writer"]["source_run_id"]
        == "failed-1"
    )
    assert update["$inc"] == {
        "completed_node_count": 1,
        "reused_node_count": 1,
    }


@pytest.mark.asyncio
async def test_retry_reuses_completed_llm_node_without_new_tokens(stub_llm):
    spec = load_workflow_from_string(
        """
name: token_saving_retry
version: "1.0"
nodes:
  - id: writer
    type: TransformAgent
    config:
      model: claude-opus-5
      prompt_template: Write a proposal section.
entry: writer
exit: writer
"""
    )
    bus = RunEventBus()
    published = []

    async def capture(evt: RunEvent) -> None:
        published.append(evt)

    bus.publish = capture
    result = await run_workflow(
        spec,
        inputs={"topic": "bioeconomy"},
        session_id="user@example.com",
        services={"llm": stub_llm, "event_bus": bus},
        run_id="retry-1",
        reused_node_results={
            "writer": {
                "output": {
                    "raw": "Previously completed proposal section",
                    "parsed": {},
                },
                "extra_state": {},
            }
        },
        retry_source_run_id="failed-1",
    )

    assert result["status"] == "completed"
    assert result["state"]["node_outputs"]["writer"]["raw"].startswith(
        "Previously completed"
    )
    assert stub_llm.calls == []
    assert [event.type for event in published] == [
        "node_reused",
        "run_completed",
    ]


def _pending_gate_request(db) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(services={"audit_db": db})
        )
    ))


@pytest.mark.asyncio
async def test_pending_gate_reports_not_paused_when_no_checkpoint_exists():
    db = FakeDB()
    db["run_checkpoints"].find_one.return_value = None
    user = CurrentUser(
        username="user@example.com", role=Role.CONSULTANT, session_id=None,
    )

    result = await pending_gate("run-1", _pending_gate_request(db), user)

    assert result == {"run_id": "run-1", "paused": False}


@pytest.mark.asyncio
async def test_pending_gate_for_user_requested_pause_omits_review_fields():
    db = FakeDB()
    db["run_checkpoints"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "status": "paused",
        "paused_node_id": "draft",
        "pause_kind": "user_requested",
        "pause_context": None,
        "node_results": {},
    }
    user = CurrentUser(
        username="user@example.com", role=Role.CONSULTANT, session_id=None,
    )

    result = await pending_gate("run-1", _pending_gate_request(db), user)

    assert result == {
        "run_id": "run-1",
        "paused": True,
        "pause_kind": "user_requested",
        "node_id": "draft",
    }


@pytest.mark.asyncio
async def test_pending_gate_for_subprocess_wait_omits_review_fields():
    db = FakeDB()
    db["run_checkpoints"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "status": "paused",
        "paused_node_id": "run_workflow",
        "pause_kind": "subprocess",
        "pause_context": {
            "interrupt": {
                "kind": "subprocess_pause",
                "node_id": "run_workflow",
                "child_run_id": "child-1",
            },
        },
        "node_results": {},
    }
    user = CurrentUser(
        username="user@example.com", role=Role.CONSULTANT, session_id=None,
    )

    result = await pending_gate("run-1", _pending_gate_request(db), user)

    assert result == {
        "run_id": "run-1",
        "paused": True,
        "pause_kind": "subprocess",
        "node_id": "run_workflow",
    }


@pytest.mark.asyncio
async def test_pending_gate_for_hitl_gate_returns_the_real_review_content():
    db = FakeDB()
    db["run_checkpoints"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "status": "paused",
        "paused_node_id": "approval",
        "pause_kind": "hitl_gate",
        "pause_context": {
            "interrupt": {
                "node_id": "approval",
                "question": "Approve the draft?",
                "context": {"topic": "bioeconomy"},
                "allowed_actions": ["approve", "reject", "edit"],
                "content": {"text": "Draft body", "format": "text", "source": "workflow"},
                "panels": [{
                    "label": "Topic", "field": "seed.topic", "hint": "Confirm scope.",
                    "editable": False, "value": "bioeconomy", "available": True,
                }],
                "review_purpose": "A person confirms the final scope.",
                "allow_document_override": True,
                "max_edit_chars": 50_000,
            }
        },
        "node_results": {},
    }
    user = CurrentUser(
        username="user@example.com", role=Role.CONSULTANT, session_id=None,
    )

    result = await pending_gate("run-1", _pending_gate_request(db), user)

    assert result == {
        "gate_id": "run-1:approval:",
        "run_id": "run-1",
        "parent_run_id": None,
        "paused": True,
        "pause_kind": "hitl_gate",
        "node_id": "approval",
        "question": "Approve the draft?",
        "context": {"topic": "bioeconomy"},
        "allowed_actions": ["approve", "reject", "edit"],
        "content": {"text": "Draft body", "format": "text", "source": "workflow"},
        "panels": [{
            "label": "Topic", "field": "seed.topic", "hint": "Confirm scope.",
            "editable": False, "value": "bioeconomy", "available": True,
        }],
        "review_purpose": "A person confirms the final scope.",
        "display_name": "Human review",
        "allow_document_override": True,
        "max_edit_chars": 50_000,
    }


@pytest.mark.asyncio
async def test_pending_gate_follows_subprocess_to_child_human_review():
    db = InMemoryDB()
    parent_run_id = "parent-run"
    child_run_id = "child-run"
    session_id = "user@example.com"
    db["run_checkpoints"].docs.extend([
        {
            "run_id": parent_run_id,
            "session_id": session_id,
            "status": "paused",
            "paused_node_id": "run_workflow",
            "pause_kind": "subprocess",
            "pause_context": {"interrupt": {"kind": "subprocess_pause"}},
            "node_results": {},
        },
        {
            "run_id": child_run_id,
            "session_id": session_id,
            "status": "paused",
            "paused_node_id": "approval",
            "pause_kind": "hitl_gate",
            "workflow_yaml": """
name: Child Review
version: '1.0'
entry: approval
exit: approval
nodes:
  - id: approval
    type: HumanInLoopAgent
    config:
      question: Approve?
    experience:
      display_name: Identity Ambiguity Review
edges: []
""",
            "pause_context": {"interrupt": {
                "node_id": "approval",
                "question": "Which customer account should be used?",
                "review_purpose": "The records match more than one account.",
                "context": {},
                "panels": [{
                    "label": "Customer message", "field": "start.message", "hint": "Original request",
                    "editable": False, "value": "Please check order SO-1", "available": True,
                }],
                "allowed_actions": ["approve", "reject"],
                "content": None,
                "allow_document_override": False,
                "max_edit_chars": 10_000,
            }},
            "node_results": {},
        },
    ])
    db["subprocess_launches"].docs.append({
        "_id": f"{parent_run_id}:run_workflow",
        "parent_run_id": parent_run_id,
        "parent_node_id": "run_workflow",
        "parent_session_id": session_id,
        "child_run_id": child_run_id,
        "status": "pending",
    })
    user = CurrentUser(username=session_id, role=Role.CONSULTANT, session_id=None)

    result = await pending_gate(parent_run_id, _pending_gate_request(db), user)

    assert result["run_id"] == child_run_id
    assert result["gate_id"].startswith(f"{child_run_id}:approval:")
    assert result["parent_run_id"] == parent_run_id
    assert result["node_id"] == "approval"
    assert result["display_name"] == "Identity Ambiguity Review"
    assert result["review_purpose"] == "The records match more than one account."
    assert result["panels"][0]["value"] == "Please check order SO-1"
    assert result["content"] is None


@pytest.mark.asyncio
async def test_pending_gate_falls_back_to_workflow_config_for_legacy_checkpoints():
    """Checkpoints paused before the interrupt-payload capture fix only hold
    the lossy placeholder string "<tuple>" in pause_context.interrupt (see
    tests/test_durable_hitl.py's payload regression test). The endpoint must
    not crash on that shape — it should recover allowed_actions/question/etc.
    straight from the paused node's own (static) config instead, so approve/
    reject/edit still work for a run that's already paused."""
    db = FakeDB()
    workflow_yaml = """
name: legacy_gate
version: "1.0"
nodes:
  - id: seed
    type: Literal
    config:
      value: seed-result
  - id: approval
    type: HumanInLoopAgent
    config:
      question: Approve the legacy draft?
      allowed_actions: [approve, reject, edit]
      max_edit_chars: 20000
edges:
  - from: seed
    to: approval
entry: seed
exit: approval
"""
    db["run_checkpoints"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "workflow_yaml": workflow_yaml,
        "status": "paused",
        "paused_node_id": "approval",
        "pause_kind": "hitl_gate",
        "pause_context": {"interrupt": "<tuple>"},
        "node_results": {},
    }
    user = CurrentUser(
        username="user@example.com", role=Role.CONSULTANT, session_id=None,
    )

    result = await pending_gate("run-1", _pending_gate_request(db), user)

    assert result["paused"] is True
    assert result["node_id"] == "approval"
    assert result["question"] == "Approve the legacy draft?"
    assert result["allowed_actions"] == ["approve", "reject", "edit"]
    assert result["max_edit_chars"] == 20_000
    assert result["content"] is None


@pytest.mark.asyncio
async def test_retry_api_creates_a_new_attempt_from_private_checkpoint():
    db = FakeDB()
    workflow_yaml = """
name: retry_api
version: "1.0"
nodes:
  - id: first
    type: Literal
    config:
      value: hello
entry: first
exit: first
"""
    db["run_history"].find_one.side_effect = [
        {
            "run_id": "failed-1",
            "session_id": "user@example.com",
            "workflow_name": "retry_api",
            "status": "failed",
            "attempt": 1,
            "origin": "chat_saved_workflow",
            "history_visibility": "conversation_only",
            "workflow_id": "research-helper",
            "workflow_version_id": "version-2",
            "conversation_id": "conversation-1",
            "message_id": "message-1",
        },
        None,
        {"started_at": 10.0},
    ]
    db["run_checkpoints"].find_one.return_value = {
        "run_id": "failed-1",
        "session_id": "user@example.com",
        "workflow_yaml": workflow_yaml,
        "inputs": {"topic": "bioeconomy"},
        "collection_id": "proposal",
        "node_results": {
            "first": {
                "node_id": "first",
                "output": {"value": "hello"},
                "extra_state": {},
            }
        },
    }
    request = cast(Request, SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services={
                    "audit_db": db,
                    "event_bus": RunEventBus(),
                }
            )
        )
    ))
    user = CurrentUser(
        username="user@example.com",
        role=Role.CONSULTANT,
        session_id=None,
    )

    result = await retry_failed_run(
        "failed-1",
        RetryRunRequest(run_id="retry-1"),
        request,
        user,
    )

    assert result["status"] == "completed"
    assert result["run_id"] == "retry-1"
    assert result["retry"] == {
        "source_run_id": "failed-1",
        "reused_node_count": 1,
    }
    history_updates = db["run_history"].update_one.await_args_list
    assert any(
        call.args[1].get("$set", {}).get("retry_of_run_id")
        == "failed-1"
        for call in history_updates
    )
    created_attempt = next(
        call.args[1]["$set"]
        for call in history_updates
        if call.args[1].get("$set", {}).get("retry_of_run_id") == "failed-1"
    )
    assert created_attempt["origin"] == "chat_saved_workflow"
    assert created_attempt["history_visibility"] == "conversation_only"
    assert created_attempt["workflow_id"] == "research-helper"
    assert created_attempt["workflow_version_id"] == "version-2"
    assert created_attempt["conversation_id"] == "conversation-1"
    assert created_attempt["message_id"] == "message-1"
    assert any(
        call.args[1].get("$set", {}).get("node_runs.first.status")
        == "reused"
        or (
            call.args[1].get("$set", {}).get("node_runs.first", {})
            .get("status")
            == "reused"
        )
        for call in history_updates
    )


def _delete_request(db) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services={"audit_db": db}))
    ))


def _delete_user():
    return CurrentUser(
        username="user@example.com", role=Role.CONSULTANT, session_id=None,
    )


@pytest.mark.asyncio
async def test_delete_blocks_a_running_run_younger_than_the_minimum_age():
    db = FakeDB()
    db["run_history"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "status": "running",
        "started_at": time.time() - 60,
        "updated_at": datetime.now(timezone.utc),
        "owner_pid": os.getpid(),
        "active_nodes": [],
    }

    with pytest.raises(HTTPException) as exc_info:
        await delete_run_endpoint("run-1", _delete_request(db), _delete_user())

    assert exc_info.value.status_code == 409
    assert "still running" in str(exc_info.value.detail)
    db["run_history"].delete_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_allows_a_running_run_older_than_the_minimum_age():
    db = FakeDB()
    old_started_at = time.time() - settings.run_delete_min_running_age_seconds - 3600
    db["run_history"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "status": "running",
        "started_at": old_started_at,
        "updated_at": datetime.now(timezone.utc),
        "owner_pid": os.getpid(),
        "active_nodes": [],
    }
    db["run_history"].delete_one.return_value = SimpleNamespace(deleted_count=1)

    result = await delete_run_endpoint("run-1", _delete_request(db), _delete_user())

    assert result == {"run_id": "run-1", "deleted": True}


@pytest.mark.parametrize("status", ["paused", "completed", "failed", "rejected"])
@pytest.mark.asyncio
async def test_delete_always_allowed_for_non_running_statuses_regardless_of_age(status):
    db = FakeDB()
    db["run_history"].find_one.return_value = {
        "run_id": "run-1",
        "session_id": "user@example.com",
        "status": status,
        "started_at": time.time() - 30,
        "updated_at": datetime.now(timezone.utc),
        "active_nodes": [],
    }
    db["run_history"].delete_one.return_value = SimpleNamespace(deleted_count=1)

    result = await delete_run_endpoint("run-1", _delete_request(db), _delete_user())

    assert result == {"run_id": "run-1", "deleted": True}


async def _seed_run(db: InMemoryDB, *, run_id: str, status: str, age_seconds: float, owner_pid=None):
    await db["run_history"].insert_one({
        "run_id": run_id,
        "session_id": "user@example.com",
        "status": status,
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        "owner_pid": owner_pid,
        "active_nodes": [],
    })


@pytest.mark.asyncio
async def test_cleanup_deletes_a_paused_run_stuck_past_the_threshold():
    db = InMemoryDB()
    await _seed_run(
        db, run_id="run-paused-old", status="paused",
        age_seconds=settings.run_auto_cleanup_after_seconds + 60,
    )

    deleted = await cleanup_stale_runs(db)

    assert deleted == ["run-paused-old"]
    assert await db["run_history"].find_one({"run_id": "run-paused-old"}) is None


@pytest.mark.asyncio
async def test_cleanup_deletes_a_running_run_only_when_its_owner_is_dead():
    db = InMemoryDB()
    await _seed_run(
        db, run_id="run-running-dead", status="running",
        age_seconds=settings.run_auto_cleanup_after_seconds + 60,
        owner_pid=_spawn_and_reap_dead_pid(),
    )
    await _seed_run(
        db, run_id="run-running-alive", status="running",
        age_seconds=settings.run_auto_cleanup_after_seconds + 60,
        owner_pid=os.getpid(),
    )

    deleted = await cleanup_stale_runs(db)

    assert deleted == ["run-running-dead"]
    assert await db["run_history"].find_one({"run_id": "run-running-dead"}) is None
    assert await db["run_history"].find_one({"run_id": "run-running-alive"}) is not None


@pytest.mark.asyncio
async def test_cleanup_leaves_young_running_and_paused_runs_alone():
    db = InMemoryDB()
    await _seed_run(db, run_id="run-paused-young", status="paused", age_seconds=60)
    await _seed_run(
        db, run_id="run-running-young", status="running", age_seconds=60,
        owner_pid=_spawn_and_reap_dead_pid(),
    )

    deleted = await cleanup_stale_runs(db)

    assert deleted == []
    assert await db["run_history"].find_one({"run_id": "run-paused-young"}) is not None
    assert await db["run_history"].find_one({"run_id": "run-running-young"}) is not None
