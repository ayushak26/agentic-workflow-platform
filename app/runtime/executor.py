"""Compile a WorkflowSpec and run it. Phase 5 adds:
  - service injection (LLM gateway, retriever) through `services` param
  - interrupt detection for HITL: returns {"status": "paused", ...}
  - in-process graph cache (_PAUSED_GRAPHS) so resume can rehydrate
"""
from __future__ import annotations

import uuid
from typing import Any

from .compiler import compile_workflow
from .schema import WorkflowSpec

# In-process cache of compiled graphs by run_id. Phase 11 swaps MemorySaver
# for Redis/Postgres and this cache becomes unnecessary.
_PAUSED_GRAPHS: dict[str, Any] = {}


async def run_workflow(
    spec: WorkflowSpec,
    inputs: dict[str, Any],
    session_id: str | None = None,
    services: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph = compile_workflow(spec, services=services)
    run_id = str(uuid.uuid4())
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
    final_state = await graph.ainvoke(initial_state, config=config)

    if "__interrupt__" in final_state:
        _PAUSED_GRAPHS[run_id] = graph
        return {
            "status": "paused",
            "run_id": run_id,
            "interrupt": final_state["__interrupt__"],
            "state": final_state,
        }

    return {"status": "completed", "run_id": run_id, "state": final_state}
