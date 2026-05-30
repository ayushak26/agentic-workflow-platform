"""Integration tests for compiler-emitted events through run_workflow.

Exercises Option A's event emission inside the runtime. The full HTTP+WS path
is covered in tests/test_ws_runs.py (Phase 9B).
"""
import pytest

from app.runtime.events import RunEvent, RunEventBus
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string


HELLO_YAML = """
name: hello_events
description: linear two-node smoke
version: "1.0"
inputs:
  who:
    type: text
nodes:
  - id: lit
    type: Literal
    config:
      value: "hi"
  - id: echo
    type: Echo
    config:
      template: "hello"
edges:
  - from: lit
    to: echo
"""

PARALLEL_YAML = """
name: parallel_events
description: fan-out smoke
version: "1.0"
inputs:
  who:
    type: text
nodes:
  - id: src
    type: Literal
    config:
      value: "start"
  - id: a
    type: Echo
    config:
      template: "branch a"
  - id: b
    type: Echo
    config:
      template: "branch b"
  - id: c
    type: Echo
    config:
      template: "branch c"
  - id: sink
    type: Echo
    config:
      template: "sink"
edges:
  - from: src
    to: [a, b, c]
  - from: a
    to: sink
  - from: b
    to: sink
  - from: c
    to: sink
"""


def _record_publishes(bus: RunEventBus) -> list[RunEvent]:
    """Monkey-patch bus.publish to record events in order without disturbing delivery."""
    received: list[RunEvent] = []
    real_publish = bus.publish

    async def recording(evt: RunEvent):
        received.append(evt)
        await real_publish(evt)

    bus.publish = recording
    return received


# ------------------ event ordering ------------------

@pytest.mark.asyncio
async def test_linear_workflow_emits_exact_event_sequence(stub_llm):
    """For a two-node linear workflow, events fire in exactly one valid order."""
    bus = RunEventBus()
    received = _record_publishes(bus)

    spec = load_workflow_from_string(HELLO_YAML)
    services = {"llm": stub_llm, "event_bus": bus}
    result = await run_workflow(spec, {"who": "world"}, services=services)

    assert result["status"] == "completed"
    sequence = [(e.type, e.node_id) for e in received]
    assert sequence == [
        ("node_started", "lit"),
        ("node_completed", "lit"),
        ("node_started", "echo"),
        ("node_completed", "echo"),
        ("run_completed", None),
    ]


@pytest.mark.asyncio
async def test_parallel_branches_each_emit_started_then_completed(stub_llm):
    """Fan-out: every parallel branch emits both events in correct per-branch
    order. We deliberately don't assert cross-branch interleaving — that's a
    scheduling property of LangGraph + asyncio, not of our event emission.
    Instant-return Echo nodes won't interleave; real LLM-bound nodes will."""
    bus = RunEventBus()
    received = _record_publishes(bus)

    spec = load_workflow_from_string(PARALLEL_YAML)
    services = {"llm": stub_llm, "event_bus": bus}
    await run_workflow(spec, {"who": "world"}, services=services)

    # Per-branch: each of a, b, c got [started, completed] in that order.
    for branch in ("a", "b", "c"):
        types = [e.type for e in received if e.node_id == branch]
        assert types == ["node_started", "node_completed"], (
            f"branch {branch} had events {types}, expected [started, completed]"
        )

    # Fan-in: sink starts only after all three branches complete.
    sink_start_idx = next(
        i for i, e in enumerate(received)
        if e.node_id == "sink" and e.type == "node_started"
    )
    branch_complete_indices = [
        i for i, e in enumerate(received)
        if e.node_id in {"a", "b", "c"} and e.type == "node_completed"
    ]
    assert len(branch_complete_indices) == 3
    assert all(idx < sink_start_idx for idx in branch_complete_indices), (
        "sink should start only after all three branches have completed"
    )


# ------------------ attribution ------------------

@pytest.mark.asyncio
async def test_event_run_ids_are_consistent(stub_llm):
    """All events from a single run share the same run_id — proves the
    SYSTEM.run_id propagation through state works end-to-end."""
    bus = RunEventBus()
    received = _record_publishes(bus)

    spec = load_workflow_from_string(HELLO_YAML)
    services = {"llm": stub_llm, "event_bus": bus}
    result = await run_workflow(spec, {"who": "world"}, services=services)

    run_id = result["run_id"]
    assert run_id
    assert all(e.run_id == run_id for e in received)


@pytest.mark.asyncio
async def test_node_failure_attributes_to_correct_node(stub_llm, monkeypatch):
    """When a node raises, run_failed event carries the failing node's id.
    This is the win over Option B's name-filtered event stream."""
    bus = RunEventBus()
    received = _record_publishes(bus)

    from app.nodes._stubs import EchoNode

    async def boom(self, state, resolved):
        raise ValueError("kaboom")

    monkeypatch.setattr(EchoNode, "run", boom)

    spec = load_workflow_from_string(HELLO_YAML)
    services = {"llm": stub_llm, "event_bus": bus}

    with pytest.raises(Exception):
        await run_workflow(spec, {"who": "world"}, services=services)

    node_failures = [e for e in received if e.type == "run_failed" and e.node_id == "echo"]
    assert node_failures, (
        f"expected run_failed event attributed to 'echo', got {[(e.type, e.node_id) for e in received]}"
    )
    assert "kaboom" in node_failures[0].error


# ------------------ preview content ------------------

@pytest.mark.asyncio
async def test_node_completed_events_carry_string_preview(stub_llm):
    """Every node_completed event has output_preview set to a sanitized string.
    Proves sanitize_preview is actually called in the runtime path."""
    bus = RunEventBus()
    received = _record_publishes(bus)

    spec = load_workflow_from_string(HELLO_YAML)
    services = {"llm": stub_llm, "event_bus": bus}
    await run_workflow(spec, {"who": "world"}, services=services)

    completions = [e for e in received if e.type == "node_completed"]
    assert len(completions) == 2
    for e in completions:
        assert isinstance(e.output_preview, str)
        assert e.output_preview
        assert len(e.output_preview) <= 1000


# ------------------ backward compatibility ------------------

@pytest.mark.asyncio
async def test_no_bus_emits_no_events_and_completes_normally(stub_llm):
    """Without event_bus in services, run_workflow behaves exactly as before
    Phase 9. This is what keeps the previous 34 tests green."""
    spec = load_workflow_from_string(HELLO_YAML)
    services = {"llm": stub_llm}  # no event_bus
    result = await run_workflow(spec, {"who": "world"}, services=services)
    assert result["status"] == "completed"