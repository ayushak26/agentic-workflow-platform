from __future__ import annotations
from typing import Any
from langgraph.types import Command
from .executor import _PAUSED_GRAPHS


class HITLResumeError(KeyError):
    pass


async def resume_workflow(run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    """Resume a paused workflow with the user's decision payload.

    `decision` shape: {"decision": "approve"|"reject"|"edit", ...optional fields}
    """
    if run_id not in _PAUSED_GRAPHS:
        raise HITLResumeError(f"No paused workflow with run_id={run_id}")

    graph = _PAUSED_GRAPHS[run_id]
    config = {"configurable": {"thread_id": run_id}}
    final_state = await graph.ainvoke(Command(resume=decision), config=config)

    if "__interrupt__" in final_state:
        # Workflow paused again (next HITL node). Keep the graph around.
        return {
            "status": "paused",
            "run_id": run_id,
            "interrupt": final_state["__interrupt__"],
            "state": final_state,
        }

    # Workflow completed — drop the graph from the cache
    _PAUSED_GRAPHS.pop(run_id, None)
    return {"status": "completed", "run_id": run_id, "state": final_state}