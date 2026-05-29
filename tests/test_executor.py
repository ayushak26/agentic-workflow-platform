from pathlib import Path

import app.nodes  # noqa: F401

from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow

HELLO_YAML = Path(__file__).parent.parent / "workflows" / "hello_workflow.yaml"
PARALLEL_YAML = Path(__file__).parent.parent / "workflows" / "parallel_demo.yaml"


async def test_hello_workflow_end_to_end():
    spec = load_workflow(HELLO_YAML)
    result = await run_workflow(spec, inputs={"who": "Ayush"})
    assert result["status"] == "completed"
    state = result["state"]
    assert state["node_outputs"]["second"]["text"] == "Hello, world!"
    assert state["node_outputs"]["first"]["value"] == "world"
    assert len(state["audit_log"]) == 2
    assert {entry["node_id"] for entry in state["audit_log"]} == {"first", "second"}
    assert state["workflow_name"] == "Hello Workflow"


async def test_session_id_pinned_when_provided():
    spec = load_workflow(HELLO_YAML)
    result = await run_workflow(spec, inputs={}, session_id="session-abc-123")
    assert result["state"]["session_id"] == "session-abc-123"


async def test_audit_log_records_node_metadata():
    spec = load_workflow(HELLO_YAML)
    result = await run_workflow(spec, inputs={})
    for entry in result["state"]["audit_log"]:
        assert "node_id" in entry
        assert "type_name" in entry
        assert "duration_s" in entry
        assert entry["duration_s"] >= 0


async def test_run_id_is_unique_per_invocation():
    spec = load_workflow(HELLO_YAML)
    r1 = await run_workflow(spec, inputs={}, session_id="shared-session")
    r2 = await run_workflow(spec, inputs={}, session_id="shared-session")
    assert r1["run_id"] != r2["run_id"]
    assert r1["state"]["inputs"]["SYSTEM.run_id"] != r2["state"]["inputs"]["SYSTEM.run_id"]
    assert r1["state"]["session_id"] == r2["state"]["session_id"] == "shared-session"


async def test_parallel_fanout_and_fanin():
    spec = load_workflow(PARALLEL_YAML)
    result = await run_workflow(spec, inputs={"topic": "anything"})
    state = result["state"]
    for b in ("branch_a", "branch_b", "branch_c"):
        assert b in state["node_outputs"]
        assert "agentic workflows" in state["node_outputs"][b]["text"]
    merged = state["node_outputs"]["merge"]["text"]
    assert "Branch A" in merged
    assert "Branch B" in merged
    assert "Branch C" in merged
    assert len(state["audit_log"]) == 5