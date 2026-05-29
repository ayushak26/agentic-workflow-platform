"""HumanInLoopAgent tests — exercise pause and resume via the runtime."""
import pytest

import app.nodes  # noqa: F401

from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow, HITLResumeError
from app.runtime.schema import WorkflowSpec, NodeSpec, EdgeSpec


def _build_hitl_workflow() -> WorkflowSpec:
    """Two-node workflow: a literal, then a HITL approval gate.

    Built inline (not from YAML) to keep the test self-contained."""
    return WorkflowSpec(
        name="HITL Test",
        nodes=[
            NodeSpec(id="seed", type="Literal", config={"value": "draft v1"}),
            NodeSpec(id="approval", type="HumanInLoopAgent", config={
                "question": "Approve this draft?",
                "context_fields": ["seed.value"],
                "allowed_actions": ["approve", "reject", "edit"],
            }),
        ],
        edges=[EdgeSpec(**{"from": "seed", "to": "approval"})],
    )


async def test_hitl_pauses_with_payload():
    spec = _build_hitl_workflow()
    result = await run_workflow(spec, inputs={})

    assert result["status"] == "paused"
    assert "run_id" in result
    interrupt = result["interrupt"]
    # LangGraph wraps the interrupt payload in its own envelope; the user
    # payload is the .value attribute of each Interrupt object. Shape varies
    # by langgraph version, so we just check the question made it through.
    interrupt_str = str(interrupt)
    assert "Approve this draft?" in interrupt_str
    assert "seed.value" in interrupt_str


async def test_hitl_resume_with_approve():
    spec = _build_hitl_workflow()
    paused = await run_workflow(spec, inputs={})
    assert paused["status"] == "paused"

    resumed = await resume_workflow(
        paused["run_id"],
        decision={"decision": "approve"},
    )
    assert resumed["status"] == "completed"
    state = resumed["state"]
    assert state["node_outputs"]["approval"]["decision"] == "approve"


async def test_hitl_resume_with_reject_carries_reason():
    spec = _build_hitl_workflow()
    paused = await run_workflow(spec, inputs={})
    resumed = await resume_workflow(
        paused["run_id"],
        decision={"decision": "reject", "reason": "Tone is off"},
    )
    assert resumed["status"] == "completed"
    out = resumed["state"]["node_outputs"]["approval"]
    assert out["decision"] == "reject"
    assert out["reason"] == "Tone is off"


async def test_hitl_resume_with_edit_carries_content():
    spec = _build_hitl_workflow()
    paused = await run_workflow(spec, inputs={})
    edited = {"text": "draft v2 with my edits"}
    resumed = await resume_workflow(
        paused["run_id"],
        decision={"decision": "edit", "edited_content": edited},
    )
    out = resumed["state"]["node_outputs"]["approval"]
    assert out["decision"] == "edit"
    assert out["edited_content"] == edited


async def test_hitl_resume_unknown_run_id_raises():
    with pytest.raises(HITLResumeError):
        await resume_workflow("nonexistent-run-id", decision={"decision": "approve"})


async def test_hitl_rejects_disallowed_action():
    """If the YAML restricts actions to approve/reject and the user sends edit,
    the node should raise rather than silently accept."""
    spec = WorkflowSpec(
        name="HITL Restricted",
        nodes=[
            NodeSpec(id="seed", type="Literal", config={"value": "x"}),
            NodeSpec(id="gate", type="HumanInLoopAgent", config={
                "question": "Approve?",
                "context_fields": [],
                "allowed_actions": ["approve", "reject"],   # no edit allowed
            }),
        ],
        edges=[EdgeSpec(**{"from": "seed", "to": "gate"})],
    )
    paused = await run_workflow(spec, inputs={})
    with pytest.raises(ValueError, match="disallowed decision"):
        await resume_workflow(paused["run_id"], decision={"decision": "edit"})