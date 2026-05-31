"""Compile a WorkflowSpec and run it.

Phase 9 (Option A): node-level events are emitted by wrapped node callables
inside compile_workflow. The executor stays simple — ainvoke, detect pause,
emit run-level lifecycle events.

Phase 10A: workflow latency + outcome counters. Counted at TERMINAL state, not
at invocation — a HITL pause is not a completion, so counting on invoke would
double-count every workflow that gets resumed and corrupt the success rate.
"""
from __future__ import annotations

import time as _time
import uuid
from typing import Any

from app.observability import metrics

from .compiler import compile_workflow
from .events import RunEvent, RunEventBus
from .schema import WorkflowSpec

_PAUSED_GRAPHS: dict[str, Any] = {}


async def run_workflow(
    spec: WorkflowSpec,
    inputs: dict[str, Any],
    session_id: str | None = None,
    services: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    graph = compile_workflow(spec, services=services)
    run_id = run_id or str(uuid.uuid4())
    workflow_name = spec.name
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

    start = _time.perf_counter()
    try:
        final_state = await graph.ainvoke(initial_state, config=config)
    except BaseException as e:
        # Terminal error outcome. The wrapped node already published a node-level
        # run_failed with attribution; we add a run-level one for subscribers.
        metrics.WORKFLOW_RUNS.labels(workflow=workflow_name, status="error").inc()
        if bus is not None:
            await bus.publish(
                RunEvent(type="run_failed", run_id=run_id, error=str(e)[:240])
            )
        raise
    finally:
        # Latency is recorded for every invocation, including pauses. For a
        # paused run this measures time-to-pause, which is acceptable for a
        # local POC; a stricter version would only observe on true completion.
        metrics.WORKFLOW_LATENCY.labels(workflow=workflow_name).observe(
            _time.perf_counter() - start
        )

    if "__interrupt__" in final_state:
        _PAUSED_GRAPHS[run_id] = graph
        # Paused is NOT a terminal state — no WORKFLOW_RUNS increment here.
        # The wrapped node that paused already published node_paused.
        return {
            "status": "paused",
            "run_id": run_id,
            "interrupt": final_state["__interrupt__"],
            "state": final_state,
        }

    # Terminal success outcome.
    metrics.WORKFLOW_RUNS.labels(workflow=workflow_name, status="success").inc()
    if bus is not None:
        await bus.publish(RunEvent(type="run_completed", run_id=run_id))

    return {"status": "completed", "run_id": run_id, "state": final_state}