from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.nodes  # noqa: F401
import pytest
from fastapi import HTTPException

from app.api.runs import RetryRunRequest, retry_failed_run
from app.api.workflows import _scope
from app.runtime.executor import run_workflow
from app.runtime.events import RunEventBus
from app.runtime.loader import load_workflow_from_string
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.run_history import (
    build_node_input,
    get_retry_checkpoint,
    initialize_run_checkpoint,
    record_checkpoint_node_completed,
    record_node_completed,
    record_node_reused,
    upsert_run,
)


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

    async def capture(event):
        published.append(event)

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
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services={
                    "audit_db": db,
                    "event_bus": RunEventBus(),
                }
            )
        )
    )
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
