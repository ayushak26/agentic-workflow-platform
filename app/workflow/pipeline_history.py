"""Durable pipeline-run tracking.

A pipeline run is a thin sequencing layer on top of ordinary workflow runs —
each stage is executed through the exact same path as a standalone
``POST /workflows/run`` (see ``app/workflow/orchestration.py`` and
``app/runtime/pipeline_executor.py``), so it gets its own normal
``run_history``/``run_checkpoints`` entry: Run History shows it, "Retry from
failure" works on it unmodified. This collection only tracks which run_id
belongs to which stage, and the pipeline's own gate/advance state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

TERMINAL_STAGE_STATUSES = {"completed", "failed", "rejected"}


def _require_session(session_id: str) -> str:
    if not session_id or not session_id.strip():
        raise ValueError("session_id is mandatory for pipeline history access")
    return session_id


async def ensure_pipeline_indexes(db) -> None:
    await db["pipeline_runs"].create_index("pipeline_run_id", unique=True)
    await db["pipeline_runs"].create_index(
        [("session_id", 1), ("created_at", -1)]
    )
    await db["pipeline_runs"].create_index(
        [("session_id", 1), ("stages.run_id", 1)]
    )


async def create_pipeline_run(
    db,
    *,
    pipeline_run_id: str,
    session_id: str,
    pipeline_name: str,
    pipeline_yaml: str,
    pipeline_inputs: dict[str, Any],
    stages: list[dict[str, Any]],
) -> None:
    """Create the pipeline record before its first stage starts.

    ``stages`` is the full ordered stage list, each entry
    ``{"id", "workflow", "run_id": None, "status": "pending", "error": None}``
    — created up front so the UI can show the whole plan immediately, not
    just the stage currently running.
    """
    _require_session(session_id)
    now = datetime.now(timezone.utc)
    await db["pipeline_runs"].update_one(
        {"pipeline_run_id": pipeline_run_id},
        {
            "$setOnInsert": {
                "pipeline_run_id": pipeline_run_id,
                "session_id": session_id,
                "pipeline_name": pipeline_name,
                "pipeline_yaml": pipeline_yaml,
                "pipeline_inputs": pipeline_inputs,
                "status": "running",
                "current_stage_index": 0,
                "stages": stages,
                "created_at": now,
                "updated_at": now,
            }
        },
        upsert=True,
    )


async def record_stage_launch(
    db,
    *,
    pipeline_run_id: str,
    session_id: str,
    stage_index: int,
    run_id: str,
) -> None:
    """Link a stage to the run_id about to execute it."""
    _require_session(session_id)
    await db["pipeline_runs"].update_one(
        {"pipeline_run_id": pipeline_run_id, "session_id": session_id},
        {
            "$set": {
                f"stages.{stage_index}.run_id": run_id,
                f"stages.{stage_index}.status": "running",
                f"stages.{stage_index}.error": None,
                "status": "running",
                "current_stage_index": stage_index,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def get_pipeline_run(
    db, session_id: str, pipeline_run_id: str,
) -> dict[str, Any] | None:
    _require_session(session_id)
    return await db["pipeline_runs"].find_one(
        {"session_id": session_id, "pipeline_run_id": pipeline_run_id},
        {"_id": 0},
    )


async def list_pipeline_runs(
    db, session_id: str, limit: int = 50,
) -> list[dict[str, Any]]:
    _require_session(session_id)
    cursor = (
        db["pipeline_runs"]
        .find({"session_id": session_id}, {"_id": 0, "pipeline_yaml": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


TERMINAL_PIPELINE_STATUSES = {"completed", "failed"}


async def find_active_pipeline_stage(
    db, *, run_id: str, session_id: str,
) -> dict[str, Any] | None:
    """Return the pipeline_runs doc if run_id is a stage of a non-terminal
    pipeline (status "running" or "gated"), else None.

    Used to block deleting a run that a pipeline still points at — deleting
    it out from under an in-progress pipeline would leave that stage's
    ``run_id`` referencing nothing, and a later advance/reconcile would only
    find it missing after the fact.
    """
    if not session_id:
        return None
    return await db["pipeline_runs"].find_one(
        {
            "session_id": session_id,
            "stages.run_id": run_id,
            "status": {"$nin": list(TERMINAL_PIPELINE_STATUSES)},
        },
        {"_id": 0},
    )


def _stage_outcome_status(run_status: str) -> str:
    """Map an underlying workflow run's status onto a pipeline stage status."""
    if run_status in {"completed"}:
        return "completed"
    if run_status == "paused":
        return "paused"
    if run_status == "rejected":
        return "rejected"
    return "failed"


async def reconcile_stage_completion(
    db, *, run_id: str, session_id: str,
) -> None:
    """Sync pipeline state after ANY run finalizes, whether via the pipeline's
    own launch/advance call or a standalone resume of a stage's own internal
    HITL pause.

    A no-op for the overwhelming majority of runs, which aren't part of any
    pipeline — the index on ``stages.run_id`` keeps that check cheap.
    """
    if not session_id:
        return
    doc = await db["pipeline_runs"].find_one(
        {"session_id": session_id, "stages.run_id": run_id},
    )
    if doc is None:
        return

    stages = doc.get("stages") or []
    stage_index = next(
        (i for i, stage in enumerate(stages) if stage.get("run_id") == run_id),
        None,
    )
    if stage_index is None:
        return

    from app.workflow.run_history import get_run

    stage_run = await get_run(db, session_id, run_id)
    run_status = (stage_run or {}).get("status", "failed")
    stage_status = _stage_outcome_status(run_status)
    stages[stage_index]["status"] = stage_status
    if stage_status == "failed":
        stages[stage_index]["error"] = (stage_run or {}).get("error")

    is_last_stage = stage_index == len(stages) - 1
    if stage_status == "paused":
        # Mid-stage HITL pause inside the stage's own workflow — not a
        # pipeline-level gate. Resolve it via the normal resume flow; the
        # pipeline itself is still "running" this stage.
        pipeline_status = "running"
    elif stage_status == "completed":
        pipeline_status = "completed" if is_last_stage else "gated"
    else:
        pipeline_status = "failed"

    update: dict[str, Any] = {
        "stages": stages,
        "status": pipeline_status,
        "current_stage_index": stage_index,
        "updated_at": datetime.now(timezone.utc),
    }
    if pipeline_status in {"completed", "failed"}:
        update["ended_at"] = datetime.now(timezone.utc)
    await db["pipeline_runs"].update_one(
        {"pipeline_run_id": doc["pipeline_run_id"], "session_id": session_id},
        {"$set": update},
    )
