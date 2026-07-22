"""HITL resume path.

Audit logging (Phase 11A) — deliberate design note:
This function stays a pure orchestration function. It has no session_id and
no authenticated user in scope, so the HITL audit event (hitl_approve /
hitl_reject / hitl_edit) is NOT written here. It is written by the API
handler that calls resume_workflow, where the JWT `sub` (actor) and the
caller's session_id exist. The handler uses this function's return value —
status, node_id, reason — to build the audit payload. See app/api/*resume*.
"""
from __future__ import annotations
from typing import Any
from langgraph.types import Command
from .executor import _PAUSED_GRAPHS


class HITLResumeError(KeyError):
    pass


def _find_rejection(state: dict) -> dict | None:
    """Any node output carrying decision == 'reject' means a HITL gate was rejected."""
    for node_id, out in (state.get("node_outputs") or {}).items():
        if isinstance(out, dict) and out.get("decision") == "reject":
            return {"node_id": node_id, "reason": out.get("reason")}
    return None


async def resume_workflow(run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    """Resume a paused workflow with the user's decision payload.

    `decision` shape: {"decision": "approve"|"reject"|"edit", ...optional fields}
    """
    if run_id not in _PAUSED_GRAPHS:
        raise HITLResumeError(f"No paused workflow with run_id={run_id}")

    graph = _PAUSED_GRAPHS[run_id]
    config = {"configurable": {"thread_id": run_id}}
    final_state = await graph.ainvoke(Command(resume=decision), config=config)

    # Paused again at the next HITL gate — keep the graph cached for the next resume.
    if "__interrupt__" in final_state:
        return {
            "status": "paused",
            "run_id": run_id,
            "interrupt": final_state["__interrupt__"],
            "state": final_state,
        }

    # Terminal — drop the graph from the cache.
    _PAUSED_GRAPHS.pop(run_id, None)

    # A rejected HITL gate routed to END: report it as 'rejected', not 'completed'.
    rejection = _find_rejection(final_state)
    if rejection:
        return {
            "status": "rejected",
            "run_id": run_id,
            "node_id": rejection["node_id"],
            "reason": rejection["reason"],
            "state": final_state,
        }

    # Normal completion.
    return {"status": "completed", "run_id": run_id, "state": final_state}