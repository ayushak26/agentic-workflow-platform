"""Compile a WorkflowSpec and run it.

Phase 9 (Option A): node-level events are emitted by wrapped node callables
inside compile_workflow. The executor stays simple — ainvoke, detect pause,
emit run-level lifecycle events.
"""
from __future__ import annotations

import uuid
from typing import Any

from .compiler import compile_workflow
from .events import RunEvent, RunEventBus
from .schema import WorkflowSpec

_PAUSED_GRAPHS: dict[str, Any] = {}


async def run_workflow(
    spec: WorkflowSpec,
    inputs: dict[str, Any],
    session_id: str | None = None,
    services: dict[str, Any] | None = None,
    run_id: str | None = None,    # <-- new
) -> dict[str, Any]:
    graph = compile_workflow(spec, services=services)
    run_id = run_id or str(uuid.uuid4()) 
    effective_session = session_id or str(uuid.uuid4())

    merged_inputs: dict[str, Any] = dict(inputs)
    merged_inputs["SYSTEM.run_id"] = run_id
    merged_inputs["SYSTEM.workflow_id"] = spec.name
    merged_inputs["SYSTEM.session_id"] = effective_session

    initial_state: dict[str, Any] = {
        "inputs": merged_inputs,
        "node_outputs": {},
        "audit_log": [],
        "session_id": effective_session,
        "workflow_id": spec.name,
        "workflow_name": spec.name,
    }

    config = {"configurable": {"thread_id": run_id}}
    bus: RunEventBus | None = (services or {}).get("event_bus")

    try:
        final_state = await graph.ainvoke(initial_state, config=config)
    except BaseException as e:
        # The wrapped node already published node-level run_failed with attribution.
        # We still publish a run-level run_failed so subscribers know the run terminated.
        if bus is not None:
            await bus.publish(RunEvent(type="run_failed", run_id=run_id, error=str(e)[:240]))
        raise

    if "__interrupt__" in final_state:
        _PAUSED_GRAPHS[run_id] = graph
        # The wrapped node that paused already published node_paused.
        # No run-level pause event — paused is per-node, not per-run.
        return {
            "status": "paused",
            "run_id": run_id,
            "interrupt": final_state["__interrupt__"],
            "state": final_state,
        }

    if bus is not None:
        await bus.publish(RunEvent(type="run_completed", run_id=run_id))

    return {"status": "completed", "run_id": run_id, "state": final_state}