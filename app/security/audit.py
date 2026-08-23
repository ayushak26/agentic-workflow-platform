"""Audit log writer + reader.

Every node execution and HITL decision writes one record here.

Schema (MongoDB collection: audit_log):
  run_id      str
  session_id  str
  node_id     str
  event_type  str   # node_start | node_end | node_reused | node_error | hitl_*
  actor       str   # "system" or JWT sub claim
  payload     dict  # inputs/outputs SUMMARY only — never full prompt text (IP protection)
  ts          datetime

Design invariants (these are the interview answers):
  * session_id is MANDATORY on every write and every read. A blank session
    hard-fails, mirroring how collection_id uses Field(..., min_length=1) at
    the retrieval layer. This is the single line that enforces the isolation
    boundary at the audit layer.
  * There is NO unscoped read path. read_audit_events always AND-s session_id
    into the Mongo query. An API handler physically cannot ask for "all events".
  * Append-only by discipline: this module exposes insert + read only. No
    update, no delete. Cloud hardening = WORM storage (S3 Object Lock etc.).
"""
from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Event type constants — single source of truth, referenced by the hooks.
NODE_START = "node_start"
NODE_END = "node_end"
NODE_REUSED = "node_reused"
NODE_ERROR = "node_error"
HITL_APPROVE = "hitl_approve"
HITL_REJECT = "hitl_reject"
HITL_EDIT = "hitl_edit"

# Maps a HITL decision action -> audit event type. Imported by resume_workflow.
HITL_EVENT = {
    "approve": HITL_APPROVE,
    "reject": HITL_REJECT,
    "edit": HITL_EDIT,
}


def _require_session(session_id: str) -> str:
    """The isolation boundary, enforced in ONE place.

    A blank session_id must never write an unscoped row nor read across
    sessions. Same discipline as collection_id's Field(..., min_length=1).
    """
    if not session_id or not session_id.strip():
        raise ValueError("session_id is mandatory for audit access")
    return session_id


def summarize_payload(data: dict[str, Any] | None) -> dict[str, Any]:
    """Record SHAPE, not CONTENT.

    IP protection: the audit log proves what ran without ever storing the
    client IP that ran through it. We log each key's type and length, never
    the value (which could be full prompt text or proposal content).
    """
    out: dict[str, Any] = {}
    for k, v in (data or {}).items():
        if hasattr(v, "__len__"):
            out[k] = f"{type(v).__name__}[{len(v)}]"
        else:
            out[k] = type(v).__name__
    return out


async def write_audit_event(
    db,
    run_id: str,
    session_id: str,
    node_id: str,
    event_type: str,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    """Write the audit event.

    Args:
        db: Mongo database handle.
        run_id (str): Workflow run identifier.
        session_id (str): Session scope the record belongs to.
        node_id (str): Workflow node identifier.
        event_type (str): The event type.
        actor (str): Acting username (optional, default 'system').
        payload (dict[str, Any] | None): Event or audit payload (optional, default None).
    """
    _require_session(session_id)  # hard-fail on blank BEFORE building the record
    record = {
        "run_id": run_id,
        "session_id": session_id,
        "node_id": node_id,
        "event_type": event_type,
        "actor": actor,
        "payload": payload or {},
        "ts": datetime.now(timezone.utc),
    }
    try:
        await db["audit_log"].insert_one(record)
    except Exception as exc:
        # Audit failure must never crash a run — but it must be loud.
        logger.error("audit_write_failed", error=str(exc), run_id=run_id)


async def read_audit_events(
    db,
    session_id: str,
    run_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """The ONLY read path. session_id is always AND-ed into the query.

    There is deliberately no variant that omits session_id.
    """
    _require_session(session_id)
    query: dict[str, Any] = {"session_id": session_id}
    if run_id:
        query["run_id"] = run_id
    cursor = db["audit_log"].find(query, {"_id": 0}).sort("ts", -1).limit(limit)
    return [doc async for doc in cursor]
