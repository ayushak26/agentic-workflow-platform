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

from . import sleep_guard
from .compiler import compile_workflow
from .events import RunEvent, RunEventBus
from .preflight import preflight_workflow_spec, require_preflight
from .schema import WorkflowSpec

_PAUSED_GRAPHS: dict[str, Any] = {}


def _find_rejection(state: dict[str, Any]) -> dict[str, Any] | None:
    for node_id, output in (state.get("node_outputs") or {}).items():
        if isinstance(output, dict) and output.get("decision") == "reject":
            return {"node_id": node_id, "reason": output.get("reason")}
    return None


def _project_output(
    spec: WorkflowSpec,
    state: dict[str, Any],
    original_inputs: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the caller-facing output declared by the workflow contract."""

    if spec.output is None:
        return None

    projected: dict[str, Any] = {}
    if spec.output.include_input:
        projected["input"] = original_inputs

    node_outputs = state.get("node_outputs", {})
    for item in spec.output.nodes:
        value = node_outputs.get(item.node_id)
        if item.flatten and isinstance(value, dict):
            projected.update(value)
        else:
            projected[item.node_id] = value
    return projected


async def run_workflow(
    spec: WorkflowSpec,
    inputs: dict[str, Any],
    session_id: str | None = None,
    collection_id: str = "default",
    services: dict[str, Any] | None = None,
    run_id: str | None = None,
    reused_node_results: dict[str, dict[str, Any]] | None = None,
    retry_source_run_id: str | None = None,
    hitl_resume_decisions: dict[str, dict[str, Any]] | None = None,
    resume_replay: bool = False,
) -> dict[str, Any]:
    # Final in-process safety net. API routes perform a stricter service probe
    # before creating history/checkpoints; this structural pass protects direct
    # callers, durable HITL replay, scripts, and tests as well.
    require_preflight(
        preflight_workflow_spec(
            spec,
            provided_inputs=inputs,
            services=services,
            compile_graph=False,
        )
    )

    run_id = run_id or str(uuid.uuid4())
    workflow_name = spec.name
    effective_session = session_id or str(uuid.uuid4())
    effective_collection = collection_id or "default"
    run_services = dict(services or {})
    run_services["reused_node_results"] = reused_node_results or {}
    run_services["retry_source_run_id"] = retry_source_run_id
    run_services["hitl_resume_decisions"] = hitl_resume_decisions or {}
    run_services["resume_replay"] = resume_replay
    graph = compile_workflow(
        spec,
        checkpointer=run_services.get("langgraph_checkpointer"),
        services=run_services,
    )

    merged_inputs: dict[str, Any] = dict(inputs)
    merged_inputs["SYSTEM.run_id"] = run_id
    merged_inputs["SYSTEM.workflow_id"] = spec.name
    merged_inputs["SYSTEM.session_id"] = effective_session
    merged_inputs["SYSTEM.collection_id"] = effective_collection

    initial_state: dict[str, Any] = {
        "inputs": merged_inputs,
        "node_outputs": {},
        "audit_log": [],
        "session_id": effective_session,
        "collection_id": effective_collection,
        "variables": {
            variable.name: variable.value
            for variable in spec.static_variables
        },
        "domain_state": {},
        "workflow_id": spec.name,
        "workflow_name": spec.name,
    }

    config = {"configurable": {"thread_id": run_id}}
    bus: RunEventBus | None = run_services.get("event_bus")

    start = _time.perf_counter()
    await sleep_guard.acquire()
    try:
        final_state = await graph.ainvoke(initial_state, config=config)
    except BaseException as e:
        # Terminal error outcome. The wrapped node already published a node-level
        # run_failed with attribution; we add a run-level one for subscribers.
        metrics.WORKFLOW_RUNS.labels(workflow=workflow_name, status="error").inc()
        if bus is not None:
            await bus.publish(
                RunEvent(
                    type="run_failed",
                    run_id=run_id,
                    session_id=effective_session,
                    error=str(e)[:240],
                )
            )
        raise
    finally:
        # Latency is recorded for every invocation, including pauses. For a
        # paused run this measures time-to-pause, which is acceptable for a
        # local POC; a stricter version would only observe on true completion.
        metrics.WORKFLOW_LATENCY.labels(workflow=workflow_name).observe(
            _time.perf_counter() - start
        )
        await sleep_guard.release()

    if "__interrupt__" in final_state:
        _PAUSED_GRAPHS[run_id] = graph
        # Paused is NOT a terminal state — no WORKFLOW_RUNS increment here.
        # The wrapped node that paused already published node_paused.
        result = {
            "status": "paused",
            "run_id": run_id,
            "interrupt": final_state["__interrupt__"],
            "state": final_state,
        }
        if retry_source_run_id:
            result["retry"] = {
                "source_run_id": retry_source_run_id,
                "reused_node_count": len(reused_node_results or {}),
            }
        return result

    rejection = _find_rejection(final_state)
    if rejection:
        metrics.WORKFLOW_RUNS.labels(
            workflow=workflow_name,
            status="rejected",
        ).inc()
        if bus is not None:
            await bus.publish(
                RunEvent(
                    type="run_rejected",
                    run_id=run_id,
                    session_id=effective_session,
                    node_id=rejection["node_id"],
                    error=rejection["reason"],
                )
            )
        result = {
            "status": "rejected",
            "run_id": run_id,
            "node_id": rejection["node_id"],
            "reason": rejection["reason"],
            "state": final_state,
        }
        return result

    # Terminal success outcome.
    metrics.WORKFLOW_RUNS.labels(workflow=workflow_name, status="success").inc()
    if bus is not None:
        await bus.publish(
            RunEvent(
                type="run_completed",
                run_id=run_id,
                session_id=effective_session,
            )
        )

    result = {"status": "completed", "run_id": run_id, "state": final_state}
    if retry_source_run_id:
        result["retry"] = {
            "source_run_id": retry_source_run_id,
            "reused_node_count": len(reused_node_results or {}),
        }
    projected = _project_output(spec, final_state, inputs)
    if projected is not None:
        result["output"] = projected
    return result
