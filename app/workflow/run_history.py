"""Run history store.

Persists a full record of each workflow run — inputs, outputs, status,
timing — so the UI can show "what ran, what came out, how long, what it cost".

RELATIONSHIP TO THE AUDIT LOG (this is the interview answer):
  Two stores, opposite purposes. Do not conflate them.

  audit_log (app/security/audit.py)   run_history (this file)
  ----------------------------------   ----------------------------------
  append-only, never updated           one record per run, updated on resume
  SHAPE only ("draft: str[4200]")      FULL content (the 4200 chars)
  compliance / who-approved-what       operator replay / debugging
  kept forever (WORM in cloud)         should expire (TTL in prod)  <-- see NOTE
  no IP stored                         holds client IP -> encrypted+RBAC in cloud

  Both share ONE rule: session_id is mandatory and AND-ed into every read.
  A run with no session is unqueryable-by-boundary, same as audit.

NOTE (demo scope): no TTL index today — records persist indefinitely.
In production add a TTL index on `created_at` (one line, marked below);
schema does not change. Full inputs/outputs are client IP, so content
should expire even though the audit trail does not.
"""
from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _require_session(session_id: str) -> str:
    """Same isolation boundary as the audit log, enforced in one place."""
    if not session_id or not session_id.strip():
        raise ValueError("session_id is mandatory for run history access")
    return session_id


async def ensure_indexes(db) -> None:
    """Call once at startup. run_id unique; (session_id, created_at) for listing.

    PROD TTL SEAM — to auto-expire content, add:
        await db["run_history"].create_index("created_at", expireAfterSeconds=30*24*3600)
    """
    await db["run_history"].create_index("run_id", unique=True)
    await db["run_history"].create_index([("session_id", 1), ("created_at", -1)])


async def upsert_run(
    db,
    run_id: str,
    session_id: str,
    workflow_name: str,
    status: str,                       # completed | rejected | failed
    node_types: dict | None = None,    # {node_id: type_name} for UI colour-coding
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    started_at: float | None = None,
    ended_at: float | None = None,
    node_count: int | None = None,
    error: str | None = None,
) -> None:
    """Write/refresh the run record at a terminal state.

    Upsert (not insert) because a resumed run reaches a terminal state once,
    but the same run_id may be written from more than one code path
    (completion vs rejection vs failure). Keyed on run_id — last write wins,
    matching the WORKFLOW_RUNS counter which fires only at terminal states.
    """
    _require_session(session_id)
    now = datetime.now(timezone.utc)
    doc = {
        "run_id": run_id,
        "session_id": session_id,
        "workflow_name": workflow_name,
        "status": status,
        "node_types": node_types or {},
        "inputs": inputs or {},
        "outputs": outputs or {},
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": (ended_at - started_at) if (started_at and ended_at) else None,
        "node_count": node_count,
        "error": error,
        "created_at": now,
    }
    try:
        await db["run_history"].update_one(
            {"run_id": run_id}, {"$set": doc}, upsert=True
        )
    except Exception as exc:
        # Persisting history must never crash a run — but must be loud.
        logger.error("run_history_write_failed", error=str(exc), run_id=run_id)


async def get_run(db, session_id: str, run_id: str) -> dict[str, Any] | None:
    """Single run, session-scoped. Returns None if not found IN THIS SESSION."""
    _require_session(session_id)
    return await db["run_history"].find_one(
        {"session_id": session_id, "run_id": run_id}, {"_id": 0}
    )


async def list_runs(
    db, session_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Run list for the history view — session-scoped, newest first.

    Projection omits full inputs/outputs to keep the list light; the detail
    view calls get_run() for the full content of one run.
    """
    _require_session(session_id)
    projection = {
        "_id": 0, "inputs": 0, "outputs": 0,   # summary list: metadata only
    }
    cursor = (
        db["run_history"]
        .find({"session_id": session_id}, projection)
        .sort("created_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]