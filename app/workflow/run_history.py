"""Durable workflow-run history.

The run record is created before execution starts and updated throughout the
run. This is intentionally separate from the append-only audit log:

* ``run_history`` stores operator-visible inputs, node outputs, status and
  timing so a run can be inspected while it is running and after it finishes.
* ``audit_log`` stores a content-free compliance trail.

Every read and write is session-scoped. Prompt templates, system prompts and
credentials are redacted before node inputs are stored.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TERMINAL_STATUSES = {"completed", "rejected", "failed"}

_REDACTED_KEYS = {
    "api_key",
    "authorization",
    "instructions",
    "password",
    "prompt_template",
    "secret",
    "system_prompt",
    "token",
}


def _require_session(session_id: str) -> str:
    """Enforce the same isolation boundary on every history operation."""

    if not session_id or not session_id.strip():
        raise ValueError("session_id is mandatory for run history access")
    return session_id


def _node_key(node_id: str) -> str:
    """Return a Mongo-safe map key while preserving node_id in the value."""

    return node_id.replace(".", "\uff0e").replace("$", "\uff04")


def redact_for_history(value: Any) -> Any:
    """Recursively remove prompts and credentials from operator-visible data."""

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if (
                lowered in _REDACTED_KEYS
                or lowered.endswith(
                    ("_api_key", "_password", "_secret", "_token")
                )
                or lowered.startswith(
                    ("api_key_", "password_", "secret_", "token_")
                )
            ):
                cleaned[key_text] = "<redacted>"
            else:
                cleaned[key_text] = redact_for_history(item)
        return cleaned
    if isinstance(value, list):
        return [redact_for_history(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_history(item) for item in value]
    return value


def build_node_input(
    state: dict[str, Any],
    resolved_config: dict[str, Any],
) -> dict[str, Any]:
    """Build the inspectable input for one node without duplicating all outputs.

    Full workflow inputs are stored once on the run. Full output from every
    completed node is stored once in ``outputs``. A node record therefore keeps
    the resolved, non-sensitive config plus the upstream node ids it consumed.
    Together these fields make the node invocation traceable without copying a
    large proposal into Mongo once per downstream node.
    """

    workflow_inputs = {
        key: value
        for key, value in (state.get("inputs") or {}).items()
        if not str(key).startswith("SYSTEM.")
    }
    return {
        "workflow_inputs": redact_for_history(workflow_inputs),
        "upstream_node_ids": list((state.get("node_outputs") or {}).keys()),
        "resolved_config": redact_for_history(resolved_config),
    }


async def ensure_indexes(db) -> None:
    """Create indexes used by run detail and newest-first history views."""

    await db["run_history"].create_index("run_id", unique=True)
    await db["run_history"].create_index(
        [("session_id", 1), ("created_at", -1)]
    )
    await db["run_history"].create_index(
        [("session_id", 1), ("status", 1), ("updated_at", -1)]
    )
    await db["run_checkpoints"].create_index("run_id", unique=True)
    await db["run_checkpoints"].create_index(
        [("session_id", 1), ("updated_at", -1)]
    )
    await db["run_checkpoints"].create_index(
        [("session_id", 1), ("status", 1), ("updated_at", -1)]
    )


async def upsert_run(
    db,
    run_id: str,
    session_id: str,
    workflow_name: str | None = None,
    status: str | None = None,
    node_types: dict[str, str] | None = None,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    workflow_yaml: str | None = None,
    started_at: float | None = None,
    ended_at: float | None = None,
    node_count: int | None = None,
    completed_node_count: int | None = None,
    retry_of_run_id: str | None = None,
    attempt: int | None = None,
    reused_node_count: int | None = None,
    reused_nodes: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Create or patch one run without erasing fields omitted by the caller."""

    _require_session(session_id)
    now = datetime.now(timezone.utc)

    fields: dict[str, Any] = {"updated_at": now}
    optional_fields = {
        "workflow_name": workflow_name,
        "status": status,
        "node_types": node_types,
        "inputs": redact_for_history(inputs) if inputs is not None else None,
        "outputs": redact_for_history(outputs) if outputs is not None else None,
        "workflow_yaml": workflow_yaml,
        "started_at": started_at,
        "ended_at": ended_at,
        "node_count": node_count,
        "completed_node_count": completed_node_count,
        "retry_of_run_id": retry_of_run_id,
        "attempt": attempt,
        "reused_node_count": reused_node_count,
        "reused_nodes": reused_nodes,
    }
    fields.update(
        {key: value for key, value in optional_fields.items() if value is not None}
    )

    if error is not None:
        fields["error"] = error
    elif status == "running":
        fields["error"] = None

    effective_start = started_at
    if ended_at is not None and effective_start is None:
        try:
            existing = await db["run_history"].find_one(
                {"run_id": run_id, "session_id": session_id},
                {"started_at": 1},
            )
            if existing is not None:
                effective_start = existing.get("started_at")
        except Exception as exc:
            logger.error(
                "run_history_start_time_read_failed",
                error=str(exc),
                run_id=run_id,
            )
    if ended_at is not None and effective_start is not None:
        fields["duration_s"] = max(0.0, ended_at - effective_start)

    set_on_insert: dict[str, Any] = {
        "run_id": run_id,
        "session_id": session_id,
        "created_at": now,
        "active_nodes": [],
        "completed_nodes": [],
        "node_runs": {},
        "outputs": {},
        "completed_node_count": 0,
        "reused_node_count": 0,
        "reused_nodes": [],
        "attempt": 1,
        "error": None,
    }
    for key in fields:
        set_on_insert.pop(key, None)

    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {"$set": fields, "$setOnInsert": set_on_insert},
            upsert=True,
        )
    except Exception as exc:
        logger.error("run_history_write_failed", error=str(exc), run_id=run_id)


async def record_node_started(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    type_name: str,
    node_input: dict[str, Any],
    started_at: float,
) -> None:
    """Mark one node active and persist its effective, redacted input."""

    _require_session(session_id)
    key = _node_key(node_id)
    now = datetime.now(timezone.utc)
    record = {
        "node_id": node_id,
        "type_name": type_name,
        "status": "running",
        "input": redact_for_history(node_input),
        "output": None,
        "started_at": started_at,
        "ended_at": None,
        "duration_s": None,
        "error": None,
    }
    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    f"node_runs.{key}": record,
                    "status": "running",
                    "updated_at": now,
                },
                "$addToSet": {"active_nodes": node_id},
            },
        )
    except Exception as exc:
        logger.error(
            "run_history_node_start_failed",
            error=str(exc),
            run_id=run_id,
            node_id=node_id,
        )


async def record_node_completed(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    output: dict[str, Any],
    ended_at: float,
    duration_s: float,
) -> None:
    """Persist a full node output as soon as that node completes."""

    _require_session(session_id)
    key = _node_key(node_id)
    now = datetime.now(timezone.utc)
    safe_output = redact_for_history(output)
    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    f"node_runs.{key}.status": "completed",
                    f"node_runs.{key}.output": safe_output,
                    f"node_runs.{key}.ended_at": ended_at,
                    f"node_runs.{key}.duration_s": duration_s,
                    f"outputs.{key}": safe_output,
                    "last_completed_node": node_id,
                    "updated_at": now,
                },
                "$pull": {"active_nodes": node_id},
                "$addToSet": {"completed_nodes": node_id},
                "$inc": {"completed_node_count": 1},
            },
        )
    except Exception as exc:
        logger.error(
            "run_history_node_complete_failed",
            error=str(exc),
            run_id=run_id,
            node_id=node_id,
        )


async def record_node_reused(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    type_name: str,
    output: dict[str, Any],
    source_run_id: str,
    ended_at: float,
    duration_s: float,
) -> None:
    """Persist a node result replayed from a previous failed attempt."""

    _require_session(session_id)
    key = _node_key(node_id)
    now = datetime.now(timezone.utc)
    safe_output = redact_for_history(output)
    record = {
        "node_id": node_id,
        "type_name": type_name,
        "status": "reused",
        "input": {
            "source_run_id": source_run_id,
            "note": "Output reused; provider was not called.",
        },
        "output": safe_output,
        "started_at": ended_at - duration_s,
        "ended_at": ended_at,
        "duration_s": duration_s,
        "error": None,
        "source_run_id": source_run_id,
    }
    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    f"node_runs.{key}": record,
                    f"outputs.{key}": safe_output,
                    "last_completed_node": node_id,
                    "updated_at": now,
                },
                "$pull": {"active_nodes": node_id},
                "$addToSet": {
                    "completed_nodes": node_id,
                    "reused_nodes": node_id,
                },
                "$inc": {
                    "completed_node_count": 1,
                    "reused_node_count": 1,
                },
            },
        )
    except Exception as exc:
        logger.error(
            "run_history_node_reuse_failed",
            error=str(exc),
            run_id=run_id,
            node_id=node_id,
        )


async def record_node_paused(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    paused_at: float,
) -> None:
    """Mark a HITL node and the overall run as paused."""

    _require_session(session_id)
    key = _node_key(node_id)
    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    f"node_runs.{key}.status": "paused",
                    f"node_runs.{key}.paused_at": paused_at,
                    "status": "paused",
                    "updated_at": datetime.now(timezone.utc),
                },
                "$pull": {"active_nodes": node_id},
            },
        )
    except Exception as exc:
        logger.error(
            "run_history_node_pause_failed",
            error=str(exc),
            run_id=run_id,
            node_id=node_id,
        )


async def record_node_failed(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    type_name: str,
    error: str,
    ended_at: float,
    duration_s: float,
) -> None:
    """Persist node attribution and partial run state on failure."""

    _require_session(session_id)
    key = _node_key(node_id)
    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    f"node_runs.{key}.node_id": node_id,
                    f"node_runs.{key}.type_name": type_name,
                    f"node_runs.{key}.status": "failed",
                    f"node_runs.{key}.ended_at": ended_at,
                    f"node_runs.{key}.duration_s": duration_s,
                    f"node_runs.{key}.error": error,
                    "status": "failed",
                    "error": error,
                    "failed_node": node_id,
                    "updated_at": datetime.now(timezone.utc),
                },
                "$pull": {"active_nodes": node_id},
            },
        )
    except Exception as exc:
        logger.error(
            "run_history_node_failure_write_failed",
            error=str(exc),
            run_id=run_id,
            node_id=node_id,
        )


async def initialize_run_checkpoint(
    db,
    *,
    run_id: str,
    session_id: str,
    workflow_yaml: str,
    inputs: dict[str, Any],
    collection_id: str,
    retry_of_run_id: str | None = None,
) -> None:
    """Create the private checkpoint used for zero-token node replay.

    Unlike operator-visible run history, this record retains exact inputs and
    node state deltas. It is session-scoped and is never returned by an API.
    """

    _require_session(session_id)
    now = datetime.now(timezone.utc)
    record = {
        "run_id": run_id,
        "session_id": session_id,
        "workflow_yaml": workflow_yaml,
        "inputs": inputs,
        "collection_id": collection_id,
        "retry_of_run_id": retry_of_run_id,
        "node_results": {},
        "completed_nodes": [],
        "status": "running",
        "paused_node_id": None,
        "pause_context": None,
        "approvals": [],
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db["run_checkpoints"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {"$setOnInsert": record},
            upsert=True,
        )
    except Exception as exc:
        logger.error(
            "run_checkpoint_initialize_failed",
            error=str(exc),
            run_id=run_id,
        )


async def record_checkpoint_node_completed(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    output: dict[str, Any],
    extra_state: dict[str, Any],
) -> None:
    """Save the exact replayable result immediately after node completion."""

    _require_session(session_id)
    key = _node_key(node_id)
    try:
        await db["run_checkpoints"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    f"node_results.{key}": {
                        "node_id": node_id,
                        "output": output,
                        "extra_state": extra_state,
                    },
                    "status": "running",
                    "updated_at": datetime.now(timezone.utc),
                },
                "$unset": {
                    "paused_node_id": "",
                    "pause_context": "",
                    "paused_at": "",
                },
                "$addToSet": {"completed_nodes": node_id},
            },
        )
    except Exception as exc:
        logger.error(
            "run_checkpoint_node_write_failed",
            error=str(exc),
            run_id=run_id,
            node_id=node_id,
        )


async def record_checkpoint_paused(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    pause_context: Any,
) -> None:
    """Persist enough information to resume a HITL run after process restart."""

    _require_session(session_id)
    try:
        await db["run_checkpoints"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    "status": "paused",
                    "paused_node_id": node_id,
                    "pause_context": redact_for_history(pause_context),
                    "paused_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
    except Exception as exc:
        logger.error(
            "run_checkpoint_pause_write_failed",
            error=str(exc),
            run_id=run_id,
            node_id=node_id,
        )


async def record_checkpoint_approval(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    decision: dict[str, Any],
    actor: str,
) -> None:
    """Append an immutable human decision before resuming execution."""

    _require_session(session_id)
    approval = {
        "node_id": node_id,
        "decision": redact_for_history(decision),
        "actor": actor,
        "decided_at": datetime.now(timezone.utc),
    }
    await db["run_checkpoints"].update_one(
        {
            "run_id": run_id,
            "session_id": session_id,
            "status": "paused",
            "paused_node_id": node_id,
        },
        {
            "$push": {"approvals": approval},
            "$set": {
                "status": "resuming",
                "updated_at": datetime.now(timezone.utc),
            },
        },
    )


async def mark_checkpoint_status(
    db,
    *,
    run_id: str,
    session_id: str,
    status: str,
) -> None:
    _require_session(session_id)
    update: dict[str, Any] = {
        "$set": {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }
    }
    if status in TERMINAL_STATUSES:
        update["$set"]["ended_at"] = datetime.now(timezone.utc)
        update["$unset"] = {
            "paused_node_id": "",
            "pause_context": "",
            "paused_at": "",
        }
    await db["run_checkpoints"].update_one(
        {"run_id": run_id, "session_id": session_id},
        update,
    )


async def get_retry_checkpoint(
    db,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Return a session-scoped private checkpoint for the retry endpoint."""

    _require_session(session_id)
    document = await db["run_checkpoints"].find_one(
        {"session_id": session_id, "run_id": run_id},
        {"_id": 0},
    )
    if document is None:
        return None

    reusable_results: dict[str, dict[str, Any]] = {}
    for result in (document.get("node_results") or {}).values():
        node_id = result.get("node_id")
        if node_id:
            reusable_results[node_id] = {
                "output": result.get("output") or {},
                "extra_state": result.get("extra_state") or {},
            }

    document["reusable_results"] = reusable_results
    return document


async def get_resume_checkpoint(
    db,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Return a restart-safe checkpoint only when it is currently paused."""

    document = await get_retry_checkpoint(db, session_id, run_id)
    if document is None or document.get("status") != "paused":
        return None
    if not document.get("paused_node_id"):
        return None
    return document


async def get_run(db, session_id: str, run_id: str) -> dict[str, Any] | None:
    """Return one full run if it belongs to the caller's session."""

    _require_session(session_id)
    return await db["run_history"].find_one(
        {"session_id": session_id, "run_id": run_id},
        {"_id": 0},
    )


async def list_runs(
    db,
    session_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return light run summaries, newest first, including active runs."""

    _require_session(session_id)
    projection = {
        "_id": 0,
        "inputs": 0,
        "outputs": 0,
        "node_runs": 0,
        "workflow_yaml": 0,
    }
    cursor = (
        db["run_history"]
        .find({"session_id": session_id}, projection)
        .sort("created_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]
