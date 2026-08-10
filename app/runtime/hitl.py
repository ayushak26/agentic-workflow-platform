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
from app.runtime.compiler import compile_workflow
from app.runtime.loader import load_workflow_from_string
from app.workflow.run_history import (
    clear_pause_request,
    get_resume_checkpoint,
    record_checkpoint_approval,
)

from .executor import _PAUSED_GRAPHS, run_workflow


class HITLResumeError(KeyError):
    pass


class HITLResumeConflict(RuntimeError):
    """Another worker already claimed this paused checkpoint."""


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
    return await resume_workflow_durable(run_id, decision)


def _validate_saved_decision(
    checkpoint: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    if checkpoint.get("pause_kind") == "user_requested":
        # A cooperative pause requested from run history, not a HITL gate —
        # any resume payload just continues execution past it. There is no
        # allowed_actions list to validate against because the paused node
        # need not be a HumanInLoopAgent at all.
        return
    action = decision.get("decision")
    paused_node_id = checkpoint.get("paused_node_id")
    spec = load_workflow_from_string(checkpoint["workflow_yaml"])
    node = next(
        (item for item in spec.nodes if item.id == paused_node_id),
        None,
    )
    if node is None or node.type != "HumanInLoopAgent":
        raise HITLResumeError(
            f"Saved paused node {paused_node_id!r} is not a HITL gate"
        )
    allowed = node.config.get(
        "allowed_actions",
        ["approve", "reject", "edit"],
    )
    if action not in allowed:
        raise ValueError(
            f"HITL node {paused_node_id} got disallowed decision: {action!r}"
        )


async def resume_workflow_durable(
    run_id: str,
    decision: dict[str, Any],
    *,
    services: dict[str, Any] | None = None,
    session_id: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Resume in memory when possible, otherwise rebuild from Mongo.

    The durable path replays exact completed-node results and injects the
    decision only at the saved paused gate. Replayed LLM nodes never call a
    provider and are not counted twice in the same run history record.
    """

    services = services or {}
    db = services.get("audit_db")
    checkpoint = None
    if db is not None and session_id:
        checkpoint = await get_resume_checkpoint(db, session_id, run_id)
        if checkpoint is not None:
            _validate_saved_decision(checkpoint, decision)
            claimed = await record_checkpoint_approval(
                db,
                run_id=run_id,
                session_id=session_id,
                node_id=checkpoint["paused_node_id"],
                decision=decision,
                actor=actor,
            )
            if not claimed:
                raise HITLResumeConflict(
                    f"Workflow {run_id} is already being resumed"
                )

    persistent_checkpointer = services.get("langgraph_checkpointer")
    if checkpoint is not None and persistent_checkpointer is not None:
        # Never depend on the worker-local graph when durable state exists.
        # This also guarantees the same branch is used whether resume lands on
        # the original worker or a different one.
        _PAUSED_GRAPHS.pop(run_id, None)

    if run_id not in _PAUSED_GRAPHS:
        if checkpoint is None:
            raise HITLResumeError(
                f"No durable paused workflow with run_id={run_id}"
            )

        spec = load_workflow_from_string(checkpoint["workflow_yaml"])
        if persistent_checkpointer is not None:
            # Recompile a fresh graph (the state lives in Redis, not in this
            # Python process) and resume the exact saved LangGraph thread.
            graph = compile_workflow(
                spec,
                checkpointer=persistent_checkpointer,
                services=services,
            )
            config = {"configurable": {"thread_id": run_id}}
            final_state = await graph.ainvoke(
                Command(resume=decision),
                config=config,
            )
            if "__interrupt__" in final_state:
                return {
                    "status": "paused",
                    "run_id": run_id,
                    "interrupt": final_state["__interrupt__"],
                    "state": final_state,
                    "resumed_node_id": checkpoint["paused_node_id"],
                }

            _PAUSED_GRAPHS.pop(run_id, None)
            rejection = _find_rejection(final_state)
            if rejection:
                return {
                    "status": "rejected",
                    "run_id": run_id,
                    "node_id": rejection["node_id"],
                    "reason": rejection["reason"],
                    "state": final_state,
                    "resumed_node_id": checkpoint["paused_node_id"],
                }
            return {
                "status": "completed",
                "run_id": run_id,
                "state": final_state,
                "resumed_node_id": checkpoint["paused_node_id"],
            }

        # Backward-compatible fallback for deployments that have Mongo replay
        # records but have not enabled the Redis LangGraph checkpointer yet.
        # This path skips the paused node via hitl_resume_decisions below
        # rather than through interrupt()/Command(resume=...), so a
        # cooperative pause's own flag-clear (app/runtime/compiler.py, right
        # after its interrupt() call returns) never runs here. Without this,
        # pause_requested would stay set and re-pause every later node.
        if (
            db is not None
            and session_id
            and checkpoint.get("pause_kind") == "user_requested"
        ):
            await clear_pause_request(db, run_id=run_id, session_id=session_id)

        result = await run_workflow(
            spec,
            checkpoint.get("inputs") or {},
            session_id=session_id,
            collection_id=checkpoint.get("collection_id") or "default",
            services=services,
            run_id=run_id,
            reused_node_results=checkpoint.get("reusable_results") or {},
            hitl_resume_decisions={
                checkpoint["paused_node_id"]: decision,
            },
            resume_replay=True,
        )
        result["resumed_node_id"] = checkpoint["paused_node_id"]
        return result

    graph = _PAUSED_GRAPHS[run_id]
    config = {"configurable": {"thread_id": run_id}}
    final_state = await graph.ainvoke(Command(resume=decision), config=config)

    # Paused again at the next HITL gate — keep the graph cached for the next resume.
    if "__interrupt__" in final_state:
        result = {
            "status": "paused",
            "run_id": run_id,
            "interrupt": final_state["__interrupt__"],
            "state": final_state,
        }
        if checkpoint is not None:
            result["resumed_node_id"] = checkpoint["paused_node_id"]
        return result

    # Terminal — drop the graph from the cache.
    _PAUSED_GRAPHS.pop(run_id, None)

    # A rejected HITL gate routed to END: report it as 'rejected', not 'completed'.
    rejection = _find_rejection(final_state)
    if rejection:
        result = {
            "status": "rejected",
            "run_id": run_id,
            "node_id": rejection["node_id"],
            "reason": rejection["reason"],
            "state": final_state,
        }
        if checkpoint is not None:
            result["resumed_node_id"] = checkpoint["paused_node_id"]
        return result

    # Normal completion.
    result = {"status": "completed", "run_id": run_id, "state": final_state}
    if checkpoint is not None:
        result["resumed_node_id"] = checkpoint["paused_node_id"]
    return result