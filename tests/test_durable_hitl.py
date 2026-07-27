from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.runtime.executor import _PAUSED_GRAPHS
from app.runtime.hitl import resume_workflow_durable


class FakeDB:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.collections = {}

    def __getitem__(self, name):
        if name not in self.collections:
            collection = AsyncMock()
            collection.find_one.return_value = (
                self.checkpoint if name == "run_checkpoints" else None
            )
            self.collections[name] = collection
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
    approval_updates = db["run_checkpoints"].update_one.await_args_list
    assert any(
        "$push" in call.args[1]
        and call.args[1]["$push"]["approvals"]["actor"]
        == "ayush@example.com"
        for call in approval_updates
    )
