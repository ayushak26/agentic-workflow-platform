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

import json
import os
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from app.config import settings
from app.db.migrations import CURRENT_RUN_SCHEMA_VERSION
from app.observability.logging import get_logger

logger = get_logger(__name__)

TERMINAL_STATUSES = {"completed", "rejected", "failed"}

# A run_history/run_checkpoints document holds one map entry per node for the
# lifetime of the run. Mongo caps any single document at 16MB, so a value that
# is individually large (a full LLM completion, a red-team transcript) is
# moved into GridFS instead of embedded inline; only a small pointer stays in
# the map entry. This is a per-value threshold, not a document-size check —
# it exists specifically to stop the handful of large fields from ever
# accumulating toward the 16MB cap, so it must stay well below it.
_INLINE_VALUE_LIMIT_BYTES = 200_000
_BLOB_BUCKET_NAME = "run_history_blobs"

_SIZE_ERROR_MARKERS = ("BSONObj size", "DocumentTooLarge", "too large")


def _is_document_size_error(exc: Exception) -> bool:
    """Return whether document size error.

    Args:
        exc (Exception): Exception that was raised.

    Returns:
        bool: True when document size error.
    """
    text = str(exc)
    return any(marker in text for marker in _SIZE_ERROR_MARKERS)


async def _externalize_if_large(
    db,
    *,
    run_id: str,
    node_id: str,
    field: str,
    value: Any,
    force: bool = False,
) -> Any:
    """Return `value` unchanged, or a small GridFS pointer if it's too big.

    `_inflate_value` reverses this on read. Values that already are a pointer
    (e.g. re-externalizing on a size-error retry) are returned unchanged.
    """
    if value is None or (isinstance(value, dict) and value.get("_externalized")):
        return value
    try:
        encoded = json.dumps(value, default=str).encode("utf-8")
    except (TypeError, ValueError):
        return value
    if not force and len(encoded) <= _INLINE_VALUE_LIMIT_BYTES:
        return value

    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_BLOB_BUCKET_NAME)
    file_id = await bucket.upload_from_stream(
        f"{run_id}:{node_id}:{field}",
        encoded,
        metadata={"run_id": run_id, "node_id": node_id, "field": field},
    )
    return {
        "_externalized": True,
        "blob_id": str(file_id),
        "size_bytes": len(encoded),
        "preview": encoded[:2000].decode("utf-8", errors="replace"),
    }


async def _inflate_value(db, value: Any) -> Any:
    """Reverse `_externalize_if_large`: fetch and decode a GridFS pointer."""

    if not isinstance(value, dict) or not value.get("_externalized"):
        return value
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_BLOB_BUCKET_NAME)
    try:
        stream = await bucket.open_download_stream(ObjectId(value["blob_id"]))
        raw = await stream.read()
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        logger.error(
            "run_history_blob_read_failed",
            error=str(exc),
            blob_id=value.get("blob_id"),
        )
        return value


async def _inflate_run_document(
    db, doc: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Transparently resolve GridFS pointers so callers see the original shape."""

    if doc is None:
        return doc
    outputs = doc.get("outputs")
    if isinstance(outputs, dict):
        if outputs.get("_externalized"):
            doc["outputs"] = await _inflate_value(db, outputs)
        else:
            doc["outputs"] = {
                key: await _inflate_value(db, value) for key, value in outputs.items()
            }
    node_runs = doc.get("node_runs")
    if isinstance(node_runs, dict):
        for record in node_runs.values():
            if not isinstance(record, dict):
                continue
            if "output" in record:
                record["output"] = await _inflate_value(db, record["output"])
            if "input" in record:
                record["input"] = await _inflate_value(db, record["input"])
    return doc

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
    await db["run_history"].create_index(
        [("session_id", 1), ("workflow_name", 1), ("created_at", -1)]
    )
    await db["run_checkpoints"].create_index("run_id", unique=True)
    await db["run_checkpoints"].create_index(
        [("session_id", 1), ("updated_at", -1)]
    )
    await db["run_checkpoints"].create_index(
        [("session_id", 1), ("status", 1), ("updated_at", -1)]
    )
    await db["workflow_input_files"].create_index(
        [("session_id", 1), ("uploaded_at", -1)]
    )


async def upsert_run(
    db,
    run_id: str,
    session_id: str,
    workflow_name: str | None = None,
    status: str | None = None,
    node_types: dict[str, str] | None = None,
    inputs: dict[str, Any] | None = None,
    variables: dict[str, Any] | None = None,
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
    pipeline_run_id: str | None = None,
    pipeline_name: str | None = None,
    stage_id: str | None = None,
    stage_index: int | None = None,
    total_stages: int | None = None,
) -> None:
    """Create or patch one run without erasing fields omitted by the caller."""

    _require_session(session_id)
    now = datetime.now(timezone.utc)

    stored_outputs = None
    if outputs is not None:
        stored_outputs = await _externalize_if_large(
            db,
            run_id=run_id,
            node_id="_run",
            field="outputs",
            value=redact_for_history(outputs),
        )

    fields: dict[str, Any] = {"updated_at": now}
    optional_fields = {
        "workflow_name": workflow_name,
        "status": status,
        "node_types": node_types,
        "inputs": redact_for_history(inputs) if inputs is not None else None,
        "variables": (
            redact_for_history(variables)
            if variables is not None
            else None
        ),
        "outputs": stored_outputs,
        "workflow_yaml": workflow_yaml,
        "started_at": started_at,
        "ended_at": ended_at,
        "node_count": node_count,
        "completed_node_count": completed_node_count,
        "retry_of_run_id": retry_of_run_id,
        "attempt": attempt,
        "reused_node_count": reused_node_count,
        "reused_nodes": reused_nodes,
        "pipeline_run_id": pipeline_run_id,
        "pipeline_name": pipeline_name,
        "stage_id": stage_id,
        "stage_index": stage_index,
        "total_stages": total_stages,
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
        "schema_version": CURRENT_RUN_SCHEMA_VERSION,
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
        if not _is_document_size_error(exc) or outputs is None:
            logger.error("run_history_write_failed", error=str(exc), run_id=run_id)
            return
        fields["outputs"] = await _externalize_if_large(
            db,
            run_id=run_id,
            node_id="_run",
            field="outputs",
            value=redact_for_history(outputs),
            force=True,
        )
        try:
            await db["run_history"].update_one(
                {"run_id": run_id, "session_id": session_id},
                {"$set": fields, "$setOnInsert": set_on_insert},
                upsert=True,
            )
        except Exception as retry_exc:
            logger.error(
                "run_history_write_failed", error=str(retry_exc), run_id=run_id
            )


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
    safe_input = redact_for_history(node_input)
    stored_input = await _externalize_if_large(
        db, run_id=run_id, node_id=node_id, field="input", value=safe_input
    )
    record = {
        "node_id": node_id,
        "type_name": type_name,
        "status": "running",
        "input": stored_input,
        "output": None,
        "started_at": started_at,
        "ended_at": None,
        "duration_s": None,
        "error": None,
    }
    # `owner_pid` records which OS process is actually executing this node
    # right now. It is the liveness signal _reconcile_if_stale uses to tell
    # "the owning process died" (pid on the doc no longer exists / doesn't
    # match this process) apart from "this node is just slow" (pid still
    # matches — the process is demonstrably still alive and working).
    update = {
        "$set": {
            f"node_runs.{key}": record,
            "status": "running",
            "updated_at": now,
            "owner_pid": os.getpid(),
        },
        "$addToSet": {"active_nodes": node_id},
    }
    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id}, update
        )
    except Exception as exc:
        if not _is_document_size_error(exc):
            logger.error(
                "run_history_node_start_failed",
                error=str(exc),
                run_id=run_id,
                node_id=node_id,
            )
            return
        record["input"] = await _externalize_if_large(
            db, run_id=run_id, node_id=node_id, field="input", value=safe_input, force=True
        )
        update["$set"][f"node_runs.{key}"] = record
        try:
            await db["run_history"].update_one(
                {"run_id": run_id, "session_id": session_id}, update
            )
        except Exception as retry_exc:
            logger.error(
                "run_history_node_start_failed",
                error=str(retry_exc),
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
    model_selections: list[dict[str, Any]] | None = None,
    ended_at: float,
    duration_s: float,
) -> None:
    """Persist a full node output as soon as that node completes."""

    _require_session(session_id)
    key = _node_key(node_id)
    now = datetime.now(timezone.utc)
    safe_output = redact_for_history(output)

    async def _build_set(force: bool) -> dict[str, Any]:
        """Build the set.

        Args:
            force (bool): Force flag.

        Returns:
            dict[str, Any]: The set.
        """
        stored_output = await _externalize_if_large(
            db, run_id=run_id, node_id=node_id, field="output", value=safe_output, force=force
        )
        return {
            f"node_runs.{key}.status": "completed",
            f"node_runs.{key}.output": stored_output,
            f"node_runs.{key}.model_selections": (
                redact_for_history(model_selections or [])
            ),
            f"node_runs.{key}.ended_at": ended_at,
            f"node_runs.{key}.duration_s": duration_s,
            f"outputs.{key}": stored_output,
            "last_completed_node": node_id,
            "updated_at": now,
        }

    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": await _build_set(force=False),
                "$pull": {"active_nodes": node_id},
                "$addToSet": {"completed_nodes": node_id},
                "$inc": {"completed_node_count": 1},
            },
        )
    except Exception as exc:
        if not _is_document_size_error(exc):
            logger.error(
                "run_history_node_complete_failed",
                error=str(exc),
                run_id=run_id,
                node_id=node_id,
            )
            return
        try:
            await db["run_history"].update_one(
                {"run_id": run_id, "session_id": session_id},
                {
                    "$set": await _build_set(force=True),
                    "$pull": {"active_nodes": node_id},
                    "$addToSet": {"completed_nodes": node_id},
                    "$inc": {"completed_node_count": 1},
                },
            )
        except Exception as retry_exc:
            logger.error(
                "run_history_node_complete_failed",
                error=str(retry_exc),
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

    async def _build_set(force: bool) -> dict[str, Any]:
        """Build the set.

        Args:
            force (bool): Force flag.

        Returns:
            dict[str, Any]: The set.
        """
        stored_output = await _externalize_if_large(
            db, run_id=run_id, node_id=node_id, field="output", value=safe_output, force=force
        )
        record = {
            "node_id": node_id,
            "type_name": type_name,
            "status": "reused",
            "input": {
                "source_run_id": source_run_id,
                "note": "Output reused; provider was not called.",
            },
            "output": stored_output,
            "started_at": ended_at - duration_s,
            "ended_at": ended_at,
            "duration_s": duration_s,
            "error": None,
            "source_run_id": source_run_id,
        }
        return {
            f"node_runs.{key}": record,
            f"outputs.{key}": stored_output,
            "last_completed_node": node_id,
            "updated_at": now,
        }

    update_tail = {
        "$pull": {"active_nodes": node_id},
        "$addToSet": {
            "completed_nodes": node_id,
            "reused_nodes": node_id,
        },
        "$inc": {
            "completed_node_count": 1,
            "reused_node_count": 1,
        },
    }
    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {"$set": await _build_set(force=False), **update_tail},
        )
    except Exception as exc:
        if not _is_document_size_error(exc):
            logger.error(
                "run_history_node_reuse_failed",
                error=str(exc),
                run_id=run_id,
                node_id=node_id,
            )
            return
        try:
            await db["run_history"].update_one(
                {"run_id": run_id, "session_id": session_id},
                {"$set": await _build_set(force=True), **update_tail},
            )
        except Exception as retry_exc:
            logger.error(
                "run_history_node_reuse_failed",
                error=str(retry_exc),
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
    pause_kind: str = "hitl_gate",
) -> None:
    """Mark a node and the overall run as paused.

    ``pause_kind`` distinguishes a HITL gate's own interrupt ("hitl_gate",
    the default — resumed with an approve/reject/edit decision) from a
    cooperative pause requested from run history ("user_requested" — resumed
    with a plain continue, at whatever node boundary the run happened to
    reach). Both go through the same LangGraph interrupt/resume machinery;
    this field only changes how the resume payload is validated and how the
    UI labels the pause.
    """

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
                    "pause_kind": pause_kind,
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


async def request_pause(db, *, run_id: str, session_id: str) -> bool:
    """Ask a running workflow to pause at its next node boundary.

    Best-effort and asynchronous: this only sets a flag the executor checks
    between node invocations (see app/runtime/compiler.py) — it cannot
    interrupt a node that is already mid-execution (e.g. an in-flight LLM
    call). Returns whether a currently-"running" run matched.
    """

    _require_session(session_id)
    result = await db["run_history"].update_one(
        {"run_id": run_id, "session_id": session_id, "status": "running"},
        {
            "$set": {
                "pause_requested": True,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return result.matched_count > 0


async def clear_pause_request(db, *, run_id: str, session_id: str) -> None:
    """Clear the pause request.

    Args:
        db: Mongo database handle.
        run_id (str): Workflow run identifier.
        session_id (str): Session scope the record belongs to.
    """
    _require_session(session_id)
    await db["run_history"].update_one(
        {"run_id": run_id, "session_id": session_id},
        {
            "$set": {
                "pause_requested": False,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def is_pause_requested(db, *, run_id: str, session_id: str) -> bool:
    """Cheap per-node check — the executor calls this before every node."""

    doc = await db["run_history"].find_one(
        {"run_id": run_id, "session_id": session_id},
        {"pause_requested": 1},
    )
    return bool(doc and doc.get("pause_requested"))


async def record_node_failed(
    db,
    *,
    run_id: str,
    session_id: str,
    node_id: str,
    type_name: str,
    error: str,
    error_type: str | None = None,
    error_traceback: str | None = None,
    model_selections: list[dict[str, Any]] | None = None,
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
                    f"node_runs.{key}.error_type": error_type,
                    f"node_runs.{key}.error_traceback": error_traceback,
                    f"node_runs.{key}.model_selections": (
                        redact_for_history(model_selections or [])
                    ),
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
        "schema_version": CURRENT_RUN_SCHEMA_VERSION,
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

    async def _build_set(force: bool) -> dict[str, Any]:
        """Build the set.

        Args:
            force (bool): Force flag.

        Returns:
            dict[str, Any]: The set.
        """
        return {
            f"node_results.{key}": {
                "node_id": node_id,
                "output": await _externalize_if_large(
                    db, run_id=run_id, node_id=node_id, field="output", value=output, force=force
                ),
                "extra_state": await _externalize_if_large(
                    db,
                    run_id=run_id,
                    node_id=node_id,
                    field="extra_state",
                    value=extra_state,
                    force=force,
                ),
            },
            "status": "running",
            "updated_at": datetime.now(timezone.utc),
        }

    update_tail = {
        "$unset": {
            "paused_node_id": "",
            "pause_context": "",
            "paused_at": "",
        },
        "$addToSet": {"completed_nodes": node_id},
    }
    try:
        await db["run_checkpoints"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {"$set": await _build_set(force=False), **update_tail},
        )
    except Exception as exc:
        if not _is_document_size_error(exc):
            logger.error(
                "run_checkpoint_node_write_failed",
                error=str(exc),
                run_id=run_id,
                node_id=node_id,
            )
            return
        try:
            await db["run_checkpoints"].update_one(
                {"run_id": run_id, "session_id": session_id},
                {"$set": await _build_set(force=True), **update_tail},
            )
        except Exception as retry_exc:
            logger.error(
                "run_checkpoint_node_write_failed",
                error=str(retry_exc),
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
    pause_kind: str = "hitl_gate",
) -> None:
    """Persist enough information to resume a paused run after process restart.

    See ``record_node_paused`` for what ``pause_kind`` means.
    """

    _require_session(session_id)
    try:
        await db["run_checkpoints"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {
                "$set": {
                    "status": "paused",
                    "paused_node_id": node_id,
                    "pause_context": redact_for_history(pause_context),
                    "pause_kind": pause_kind,
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
) -> bool:
    """Append an immutable human decision before resuming execution."""

    _require_session(session_id)
    approval = {
        "node_id": node_id,
        "decision": redact_for_history(decision),
        "actor": actor,
        "decided_at": datetime.now(timezone.utc),
    }
    result = await db["run_checkpoints"].update_one(
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
    return getattr(result, "modified_count", 0) == 1


async def mark_checkpoint_status(
    db,
    *,
    run_id: str,
    session_id: str,
    status: str,
) -> None:
    """Mark the checkpoint status.

    Args:
        db: Mongo database handle.
        run_id (str): Workflow run identifier.
        session_id (str): Session scope the record belongs to.
        status (str): Status value.
    """
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
                "output": await _inflate_value(db, result.get("output")) or {},
                "extra_state": await _inflate_value(db, result.get("extra_state")) or {},
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


def _as_aware_utc(value: datetime) -> datetime:
    """Internal helper for the as aware utc step.

    Args:
        value (datetime): Value to process.

    Returns:
        datetime: The aware utc.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _process_is_alive(pid: int | None) -> bool:
    """Best-effort same-host liveness check for a run's owning process.

    ``os.kill(pid, 0)`` sends no signal — it only asks the OS whether a
    process with this pid currently exists. This is a real liveness signal
    (unlike a "no progress" timer): a single node awaiting one long LLM call
    legitimately produces no state writes for many minutes, but the process
    running it is still there the whole time.

    Caveats, by design rather than oversight: this only means something on
    the host that wrote ``owner_pid`` — it can't distinguish "hung forever
    but technically alive" from genuinely healthy, and it isn't meaningful
    across a multi-host/multi-pod deployment (a pid on another host is a
    coincidence, not evidence). It exists to catch the concrete case this
    platform actually hits: a dev server (``uvicorn --reload``) restarting
    mid-run and leaving a "running" row nothing will ever finish.
    """
    if pid is None:
        return True  # unknown owner — never guess it's dead
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by a different user
    except OSError:
        return True  # ambiguous — don't fail a run on an unrelated OS error
    return True


async def _reconcile_if_stale(db, doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flip an orphaned "running" run to "failed" the moment it is read.

    A run can be left stuck at status="running" forever if the process
    executing it dies or restarts mid-node (e.g. a dev server reload) —
    nothing is left to write the failure. Rather than run a background
    sweep, every read lazily checks staleness — but staleness is decided by
    whether the owning OS process (`owner_pid`, stamped per node start by
    record_node_started) still exists, not by elapsed time alone. A long
    LLM call can legitimately leave `updated_at` untouched for many minutes
    with the process very much alive; only a dead owner_pid means the run is
    actually orphaned. `stale_run_after_seconds` is kept as a grace period
    before the first check (a run that's mere seconds old shouldn't be
    judged at all), not as the failure trigger itself.
    """

    if doc is None or doc.get("status") != "running":
        return doc
    updated_at = doc.get("updated_at")
    if not isinstance(updated_at, datetime):
        return doc

    now = datetime.now(timezone.utc)
    age_seconds = (now - _as_aware_utc(updated_at)).total_seconds()
    if age_seconds < settings.stale_run_after_seconds:
        return doc

    owner_pid = doc.get("owner_pid")
    if _process_is_alive(owner_pid):
        return doc

    run_id = doc["run_id"]
    session_id = doc["session_id"]
    error_message = (
        f"Run marked failed automatically: the process executing it "
        f"(pid={owner_pid}) is no longer running. It crashed or the server "
        "restarted mid-execution."
    )

    fields: dict[str, Any] = {
        "status": "failed",
        "error": error_message,
        "ended_at": now,
        "updated_at": now,
        "active_nodes": [],
    }
    for node_id in doc.get("active_nodes") or []:
        key = _node_key(node_id)
        fields[f"node_runs.{key}.status"] = "failed"
        fields[f"node_runs.{key}.error"] = error_message
        fields[f"node_runs.{key}.ended_at"] = now.timestamp()

    try:
        await db["run_history"].update_one(
            {"run_id": run_id, "session_id": session_id, "status": "running"},
            {"$set": fields},
        )
        await db["run_checkpoints"].update_one(
            {"run_id": run_id, "session_id": session_id},
            {"$set": {"status": "failed", "updated_at": now}},
        )
    except Exception as exc:
        logger.error(
            "run_history_stale_reconcile_failed",
            error=str(exc),
            run_id=run_id,
        )
        return doc

    logger.warning(
        "run_history.stale_run_marked_failed",
        run_id=run_id,
        age_seconds=age_seconds,
        owner_pid=owner_pid,
    )
    doc.update(
        {
            "status": "failed",
            "error": error_message,
            "ended_at": now,
            "active_nodes": [],
        }
    )

    # This run may be a pipeline stage. The normal completion path
    # (app/workflow/orchestration.py) always syncs the parent pipeline via
    # reconcile_stage_completion, but that path never runs here — without
    # this call, a pipeline whose stage run got orphaned would stay
    # status="running" forever, permanently blocking deletion of the run
    # (see find_active_pipeline_stage in pipeline_history.py).
    from app.workflow.pipeline_history import reconcile_stage_completion

    try:
        await reconcile_stage_completion(db, run_id=run_id, session_id=session_id)
    except Exception as exc:
        logger.error(
            "run_history.stale_reconcile_pipeline_sync_failed",
            error=str(exc),
            run_id=run_id,
        )

    return doc


async def get_run(db, session_id: str, run_id: str) -> dict[str, Any] | None:
    """Return one full run if it belongs to the caller's session."""

    _require_session(session_id)
    doc = await db["run_history"].find_one(
        {"session_id": session_id, "run_id": run_id},
        {"_id": 0},
    )
    doc = await _reconcile_if_stale(db, doc)
    return await _inflate_run_document(db, doc)


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
    docs = [doc async for doc in cursor]
    return [await _reconcile_if_stale(db, doc) for doc in docs]


_MIN_SAMPLE_FOR_ESTIMATES = 3


async def workflow_stats(
    db,
    session_id: str,
    workflow_name: str,
    *,
    sample_limit: int = 200,
) -> dict[str, Any]:
    """Library "Runs and performance" tab: success rate, median duration, and
    the most common failure, computed from this session's own run history for
    one workflow. Session-scoped like every other read in this module — no
    cross-session exposure. Returns an explicit "not enough data" signal
    below a minimum sample size rather than a misleading number.
    """

    _require_session(session_id)
    projection = {
        "_id": 0,
        "run_id": 1,
        "status": 1,
        "duration_s": 1,
        "error": 1,
        "failed_node": 1,
        "created_at": 1,
    }
    cursor = (
        db["run_history"]
        .find({"session_id": session_id, "workflow_name": workflow_name}, projection)
        .sort("created_at", -1)
        .limit(sample_limit)
    )
    docs = [doc async for doc in cursor]
    # Sorted client-side too (not just via the query's own `.sort()`) so
    # "last run"/"last successful run" are correct regardless of driver.
    docs.sort(
        key=lambda doc: doc.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    completed = [doc for doc in docs if doc.get("status") == "completed"]
    failed = [doc for doc in docs if doc.get("status") == "failed"]
    terminal_count = len(completed) + len(failed)
    durations = sorted(
        doc["duration_s"]
        for doc in completed
        if isinstance(doc.get("duration_s"), (int, float))
    )

    def _median(values: list[float]) -> float | None:
        """Internal helper for the median step.

        Args:
            values (list[float]): The values.

        Returns:
            float | None: The result.
        """
        if not values:
            return None
        mid = len(values) // 2
        if len(values) % 2 == 1:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2

    failure_counts: dict[str, int] = {}
    for doc in failed:
        label = doc.get("failed_node") or doc.get("error") or "Unknown"
        failure_counts[label] = failure_counts.get(label, 0) + 1
    most_common_failure = (
        max(failure_counts.items(), key=lambda item: item[1])[0]
        if failure_counts
        else None
    )

    last_run = docs[0] if docs else None
    last_successful_run = next(
        (doc for doc in docs if doc.get("status") == "completed"), None
    )
    enough_data = terminal_count >= _MIN_SAMPLE_FOR_ESTIMATES

    return {
        "sample_size": len(docs),
        "completed_runs": len(completed),
        "failed_runs": len(failed),
        "enough_data_for_estimates": enough_data,
        "success_rate": (len(completed) / terminal_count) if enough_data else None,
        "median_duration_s": _median(durations) if enough_data else None,
        "most_common_failure": most_common_failure if enough_data else None,
        "last_run_at": last_run.get("created_at") if last_run else None,
        "last_run_status": last_run.get("status") if last_run else None,
        "last_successful_run_at": (
            last_successful_run.get("created_at") if last_successful_run else None
        ),
    }


async def _delete_run_blobs(db, *, run_id: str) -> None:
    """Remove every GridFS blob externalized for this run (see
    ``_externalize_if_large``). Best-effort end to end — deleting the run
    record itself must not be blocked by any GridFS failure, including
    bucket construction or listing, not just an individual blob delete."""

    try:
        bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_BLOB_BUCKET_NAME)
        cursor = db[f"{_BLOB_BUCKET_NAME}.files"].find(
            {"metadata.run_id": run_id}, {"_id": 1}
        )
        async for file_doc in cursor:
            try:
                await bucket.delete(file_doc["_id"])
            except Exception as exc:
                logger.error(
                    "run_history_blob_delete_failed",
                    error=str(exc),
                    run_id=run_id,
                    blob_id=str(file_doc["_id"]),
                )
    except Exception as exc:
        logger.error(
            "run_history_blob_cleanup_failed",
            error=str(exc),
            run_id=run_id,
        )


async def delete_run(db, *, run_id: str, session_id: str) -> bool:
    """Permanently remove a run's history, checkpoint, and externalized blobs.

    Returns whether a matching run_history document existed. Callers are
    responsible for checking the run isn't a live pipeline stage first (see
    app/workflow/pipeline_history.py) — this function only deletes.
    """

    _require_session(session_id)
    await _delete_run_blobs(db, run_id=run_id)
    checkpoint_result = await db["run_checkpoints"].delete_one(
        {"run_id": run_id, "session_id": session_id}
    )
    history_result = await db["run_history"].delete_one(
        {"run_id": run_id, "session_id": session_id}
    )
    return history_result.deleted_count > 0 or checkpoint_result.deleted_count > 0


async def cleanup_stale_runs(db) -> list[str]:
    """Periodic sweep (see the background loop started in app/main.py) that
    hard-deletes runs stuck in "running" or "paused" for at least
    ``settings.run_auto_cleanup_after_seconds``.

    A "paused" run is deleted by age alone — nothing owns a paused run once
    its process has parked it awaiting resume/HITL, unlike a "running" run.
    A "running" run is deleted only if it's ALSO confirmed orphaned (dead
    owner_pid, via _process_is_alive) — the same signal _reconcile_if_stale
    uses — so a genuinely still-executing long job is never touched, even if
    the sweep interval catches it mid-run.

    Any matching run that is a pipeline stage has its parent pipeline synced
    via reconcile_stage_completion before deletion, exactly like a normal
    stale-reconcile would, so deleting it never leaves a pipeline pointing at
    a run_id that no longer exists.

    Returns the run_ids actually deleted.
    """
    from app.workflow.pipeline_history import reconcile_stage_completion

    cutoff = datetime.now(timezone.utc).timestamp() - settings.run_auto_cleanup_after_seconds
    cursor = db["run_history"].find(
        {"status": {"$in": ["running", "paused"]}},
        {"_id": 0, "run_id": 1, "session_id": 1, "status": 1, "updated_at": 1, "owner_pid": 1},
    )

    deleted: list[str] = []
    async for doc in cursor:
        updated_at = doc.get("updated_at")
        if not isinstance(updated_at, datetime):
            continue
        if _as_aware_utc(updated_at).timestamp() > cutoff:
            continue
        if doc.get("status") == "running" and _process_is_alive(doc.get("owner_pid")):
            continue

        run_id = doc["run_id"]
        session_id = doc["session_id"]
        try:
            await reconcile_stage_completion(db, run_id=run_id, session_id=session_id)
            await delete_run(db, run_id=run_id, session_id=session_id)
        except Exception as exc:
            logger.error(
                "run_history.auto_cleanup_failed",
                error=str(exc),
                run_id=run_id,
            )
            continue

        logger.warning(
            "run_history.auto_deleted_stale_run",
            run_id=run_id,
            status=doc.get("status"),
        )
        deleted.append(run_id)

    return deleted