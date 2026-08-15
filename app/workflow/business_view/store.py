"""Durable state the Business View adds to a run: narration cache, notes, overrides.

Three small, session-scoped writes, all appended to data the platform already
keeps. Nothing here is a new source of truth about a workflow — a note and a
route override are records of what a *person* decided, which the execution
history has no other way to hold.

The narration cache is separate because it is disposable: losing it costs one
cheap model call, so it lives in its own collection and is keyed by the
projection's `state_version` (§17).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

NARRATION_COLLECTION = "business_narrations"


async def ensure_business_view_indexes(db: Any) -> None:
    """One lookup key, one TTL-free cache — cheap to rebuild, so no history."""
    await db[NARRATION_COLLECTION].create_index(
        [("run_id", 1), ("session_id", 1), ("state_version", 1)],
        unique=True,
        name="narration_state",
    )


async def get_cached_narration(
    db: Any, *, run_id: str, session_id: str, state_version: str,
) -> dict[str, Any] | None:
    if db is None or not state_version:
        return None
    try:
        return await db[NARRATION_COLLECTION].find_one(
            {"run_id": run_id, "session_id": session_id, "state_version": state_version},
            {"_id": 0},
        )
    except Exception as exc:
        # A cache read that fails must cost a model call, not the screen.
        logger.warning("business_narration_cache_read_failed", error=str(exc))
        return None


async def put_cached_narration(
    db: Any,
    *,
    run_id: str,
    session_id: str,
    state_version: str,
    headline: str,
    summary: str,
    next_step: str,
    source: str,
    model: str | None,
) -> None:
    if db is None or not state_version:
        return
    try:
        await db[NARRATION_COLLECTION].update_one(
            {"run_id": run_id, "session_id": session_id, "state_version": state_version},
            {
                "$set": {
                    "headline": headline,
                    "summary": summary,
                    "next_step": next_step,
                    "source": source,
                    "model": model,
                    "created_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("business_narration_cache_write_failed", error=str(exc))


async def _append_to_run(
    db: Any, *, run_id: str, session_id: str, field: str, record: dict[str, Any],
) -> dict[str, Any]:
    result = await db["run_history"].update_one(
        {"run_id": run_id, "session_id": session_id},
        {"$push": {field: record}, "$set": {"updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise LookupError("Run not found")
    return record


async def add_note(
    db: Any, *, run_id: str, session_id: str, text: str, by: str,
) -> dict[str, Any]:
    """Record something a person wants the next handler to know."""
    record = {
        "text": text.strip()[:2000],
        "by": by,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    return await _append_to_run(
        db, run_id=run_id, session_id=session_id, field="business_notes", record=record
    )


async def add_route_override(
    db: Any, *, run_id: str, session_id: str, route: str, reason: str | None, by: str,
) -> dict[str, Any]:
    """Record that a person sent this work item somewhere else.

    Deliberately does not re-execute the workflow. A person overriding where a
    case goes is a business decision about an already-finished determination;
    re-running the graph would discard everything that has been done and would
    not honour the override anyway, since the rules would reach the same
    conclusion from the same facts.
    """
    record = {
        "route": route.strip()[:120],
        "reason": (reason or "").strip()[:1000] or None,
        "by": by,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    return await _append_to_run(
        db, run_id=run_id, session_id=session_id, field="route_overrides", record=record
    )
