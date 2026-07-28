from __future__ import annotations

from copy import deepcopy
import os
from types import SimpleNamespace
import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.runtime.executor import _PAUSED_GRAPHS, run_workflow
from app.runtime.hitl import resume_workflow_durable
from app.runtime.loader import load_workflow_from_string

TWO_GATE_WORKFLOW = """
name: durable_two_gate_hitl
version: "1.0"
nodes:
  - id: seed
    type: Literal
    config:
      value: seed-result
  - id: gate_one
    type: HumanInLoopAgent
    config:
      question: Approve concept?
      allowed_actions: [approve, reject]
  - id: between
    type: Literal
    config:
      value: between-result
  - id: gate_two
    type: HumanInLoopAgent
    config:
      question: Approve final structure?
      allowed_actions: [approve, reject]
  - id: final
    type: Literal
    config:
      value: final-result
edges:
  - from: seed
    to: gate_one
  - from: gate_one
    to: between
  - from: between
    to: gate_two
  - from: gate_two
    to: final
entry: seed
exit: final
"""


def _set_path(document, path, value):
    parts = path.split(".")
    cursor = document
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = deepcopy(value)


def _unset_path(document, path):
    parts = path.split(".")
    cursor = document
    for part in parts[:-1]:
        cursor = cursor.get(part, {})
    cursor.pop(parts[-1], None)


class FakeCollection:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.updates = []

    async def find_one(self, query, projection=None):
        if self.name != "run_checkpoints":
            return None
        checkpoint = self.db.checkpoint
        if any(checkpoint.get(key) != value for key, value in query.items()):
            return None
        return deepcopy(checkpoint)

    async def update_one(self, query, update, upsert=False):
        self.updates.append((deepcopy(query), deepcopy(update)))
        checkpoint = self.db.checkpoint
        if any(
            key in {"status", "paused_node_id"}
            and checkpoint.get(key) != value
            for key, value in query.items()
        ):
            return SimpleNamespace(matched_count=0, modified_count=0)
        for path, value in update.get("$set", {}).items():
            _set_path(checkpoint, path, value)
        for path in update.get("$unset", {}):
            _unset_path(checkpoint, path)
        for path, value in update.get("$addToSet", {}).items():
            items = checkpoint.setdefault(path, [])
            if value not in items:
                items.append(deepcopy(value))
        for path, value in update.get("$push", {}).items():
            checkpoint.setdefault(path, []).append(deepcopy(value))
        return SimpleNamespace(matched_count=1, modified_count=1)


class FakeDB:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.collections = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection(self, name)
        return self.collections[name]


@pytest.mark.asyncio
async def test_paused_run_resumes_after_in_memory_graph_is_lost():
    workflow_yaml = """
name: durable_hitl
version: "1.0"
nodes:
  - id: seed
    type: Literal
    config:
      value: already completed
  - id: approval
    type: HumanInLoopAgent
    config:
      question: Approve?
      allowed_actions: [approve, reject]
edges:
  - from: seed
    to: approval
entry: seed
exit: approval
"""
    checkpoint = {
        "run_id": "run-durable",
        "session_id": "user@example.com",
        "workflow_yaml": workflow_yaml,
        "inputs": {},
        "collection_id": "default",
        "status": "paused",
        "paused_node_id": "approval",
        "node_results": {
            "seed": {
                "node_id": "seed",
                "output": {"value": "already completed"},
                "extra_state": {},
            }
        },
    }
    db = FakeDB(checkpoint)
    _PAUSED_GRAPHS.pop("run-durable", None)

    result = await resume_workflow_durable(
        "run-durable",
        {"decision": "approve"},
        services={"audit_db": db},
        session_id="user@example.com",
        actor="ayush@example.com",
    )

    assert result["status"] == "completed"
    assert result["state"]["node_outputs"]["seed"]["value"] == "already completed"
    assert result["state"]["node_outputs"]["approval"]["decision"] == "approve"
    approval_updates = db["run_checkpoints"].updates
    assert any(
        "$push" in update
        and update["$push"]["approvals"]["actor"]
        == "ayush@example.com"
        for _, update in approval_updates
    )


@pytest.mark.asyncio
async def test_persistent_checkpointer_resumes_across_two_restart_boundaries():
    workflow_yaml = TWO_GATE_WORKFLOW
    run_id = "run-two-gates"
    session_id = "user@example.com"
    checkpoint = {
        "run_id": run_id,
        "session_id": session_id,
        "workflow_yaml": workflow_yaml,
        "inputs": {},
        "collection_id": "default",
        "status": "running",
        "paused_node_id": None,
        "node_results": {},
        "completed_nodes": [],
        "approvals": [],
    }
    db = FakeDB(checkpoint)
    saver = MemorySaver()
    services = {
        "audit_db": db,
        "langgraph_checkpointer": saver,
    }

    initial = await run_workflow(
        load_workflow_from_string(workflow_yaml),
        {},
        session_id=session_id,
        services=services,
        run_id=run_id,
    )
    assert initial["status"] == "paused"
    assert db.checkpoint["paused_node_id"] == "gate_one"

    # A process restart loses the graph object, but not the durable saver.
    _PAUSED_GRAPHS.pop(run_id, None)
    after_first_gate = await resume_workflow_durable(
        run_id,
        {"decision": "approve"},
        services=services,
        session_id=session_id,
        actor="reviewer@example.com",
    )
    assert after_first_gate["status"] == "paused"
    assert db.checkpoint["paused_node_id"] == "gate_two"

    # Simulate another process restart at the second independent HITL gate.
    _PAUSED_GRAPHS.pop(run_id, None)
    completed = await resume_workflow_durable(
        run_id,
        {"decision": "approve"},
        services=services,
        session_id=session_id,
        actor="reviewer@example.com",
    )

    assert completed["status"] == "completed"
    outputs = completed["state"]["node_outputs"]
    assert outputs["seed"]["value"] == "seed-result"
    assert outputs["between"]["value"] == "between-result"
    assert outputs["final"]["value"] == "final-result"
    assert [item["node_id"] for item in db.checkpoint["approvals"]] == [
        "gate_one",
        "gate_two",
    ]

    writes = db["run_checkpoints"].updates
    seed_writes = [
        update
        for _, update in writes
        if any(
            path.startswith("node_results.seed")
            for path in update.get("$set", {})
        )
    ]
    assert len(seed_writes) == 1


@pytest.mark.asyncio
async def test_redis_checkpointer_reconnects_between_both_hitl_gates():
    """CI integration: recreate the Redis saver at every restart boundary."""

    if os.getenv("CI", "").lower() != "true" and os.getenv(
        "RUN_REDIS_HITL_INTEGRATION"
    ) != "1":
        pytest.skip("Redis restart integration runs in CI")

    redis_module = pytest.importorskip("langgraph.checkpoint.redis.aio")
    AsyncRedisSaver = redis_module.AsyncRedisSaver
    from app.config import settings

    run_id = f"redis-hitl-{uuid.uuid4()}"
    session_id = "ci@example.com"
    checkpoint = {
        "run_id": run_id,
        "session_id": session_id,
        "workflow_yaml": TWO_GATE_WORKFLOW,
        "inputs": {},
        "collection_id": "default",
        "status": "running",
        "paused_node_id": None,
        "node_results": {},
        "completed_nodes": [],
        "approvals": [],
    }
    db = FakeDB(checkpoint)

    async with AsyncRedisSaver.from_conn_string(settings.redis_url) as saver:
        await saver.asetup()
        initial = await run_workflow(
            load_workflow_from_string(TWO_GATE_WORKFLOW),
            {},
            session_id=session_id,
            services={
                "audit_db": db,
                "langgraph_checkpointer": saver,
            },
            run_id=run_id,
        )
        assert initial["status"] == "paused"

    _PAUSED_GRAPHS.pop(run_id, None)
    async with AsyncRedisSaver.from_conn_string(settings.redis_url) as saver:
        await saver.asetup()
        after_first_gate = await resume_workflow_durable(
            run_id,
            {"decision": "approve"},
            services={
                "audit_db": db,
                "langgraph_checkpointer": saver,
            },
            session_id=session_id,
            actor="ci-reviewer@example.com",
        )
        assert after_first_gate["status"] == "paused"
        assert db.checkpoint["paused_node_id"] == "gate_two"

    _PAUSED_GRAPHS.pop(run_id, None)
    async with AsyncRedisSaver.from_conn_string(settings.redis_url) as saver:
        await saver.asetup()
        completed = await resume_workflow_durable(
            run_id,
            {"decision": "approve"},
            services={
                "audit_db": db,
                "langgraph_checkpointer": saver,
            },
            session_id=session_id,
            actor="ci-reviewer@example.com",
        )
        assert completed["status"] == "completed"
        assert completed["state"]["node_outputs"]["final"]["value"] == (
            "final-result"
        )
        await saver.adelete_thread(run_id)

    seed_writes = [
        update
        for _, update in db["run_checkpoints"].updates
        if any(
            path.startswith("node_results.seed")
            for path in update.get("$set", {})
        )
    ]
    assert len(seed_writes) == 1
