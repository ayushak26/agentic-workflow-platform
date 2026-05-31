"""Audit log writer.

Every node execution and HITL decision writes one record here.

Schema (MongoDB collection: audit_log):
  run_id      str
  session_id  str
  node_id     str
  event_type  str   # node_start | node_end | node_error | hitl_approve | hitl_reject | hitl_edit
  actor       str   # system or JWT sub claim
  payload     dict  # inputs/outputs summary — never full prompt text (IP protection)
  ts          datetime
"""
from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


async def write_audit_event(
    db,
    run_id: str,
    session_id: str,
    node_id: str,
    event_type: str,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
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
        logger.error("audit_write_failed", error=str(exc), run_id=run_id)