"""One record per preflight-producing call (manual validate, generate-from-
prompt, autofix), so we can actually measure — instead of guess — how often
workflows come out with basic/repetitive structural errors and which error
codes recur most.

Deliberately global, not session-scoped (unlike run_history/pipeline_history):
the question this answers ("are we near error-free workflows?") is a
platform-wide reliability question, not a per-user one, and error codes carry
no run content/PII. Exposed read-only via GET /api/workflows/preflight-stats,
gated by require_admin rather than require_consultant for that reason.

Telemetry must never break the request it's attached to — every public
function here is a soft no-op on a missing/unreachable db (same convention as
app/workflow/orchestration.py's start_new_run_record).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

AttemptSource = Literal["validate", "generate", "autofix"]

_MIN_SAMPLE_FOR_ESTIMATES = 5


async def ensure_indexes(db: Any) -> None:
    """Ensure the indexes.

    Args:
        db (Any): Mongo database handle.
    """
    if db is None:
        return
    await db["preflight_stats"].create_index([("created_at", -1)])
    await db["preflight_stats"].create_index([("source", 1), ("created_at", -1)])


async def record_attempt(
    db: Any,
    *,
    source: AttemptSource,
    workflow_name: str | None,
    success: bool,
    error_codes: list[str],
    initial_error_codes: list[str] | None = None,
    deterministic_fixes_applied: int = 0,
    llm_attempts: int = 0,
    total_attempts: int = 1,
) -> None:
    """Record the attempt.

    Args:
        db (Any): Mongo database handle.
        source (AttemptSource): Source value.
        workflow_name (str | None): Workflow name.
        success (bool): The success.
        error_codes (list[str]): The error codes.
        initial_error_codes (list[str] | None): The initial error codes (optional, default None).
        deterministic_fixes_applied (int): The deterministic fixes applied (optional, default 0).
        llm_attempts (int): The llm attempts (optional, default 0).
        total_attempts (int): The total attempts (optional, default 1).
    """
    if db is None:
        return
    try:
        await db["preflight_stats"].insert_one({
            "created_at": datetime.now(timezone.utc),
            "source": source,
            "workflow_name": workflow_name,
            "success": success,
            "error_codes": error_codes,
            "initial_error_codes": (
                initial_error_codes if initial_error_codes is not None else error_codes
            ),
            "deterministic_fixes_applied": deterministic_fixes_applied,
            "llm_attempts": llm_attempts,
            "total_attempts": total_attempts,
        })
    except Exception:
        pass


async def preflight_stats(
    db: Any, *, since: datetime | None = None, sample_limit: int = 2000,
) -> dict[str, Any]:
    """Compute the preflight stats.

    Args:
        db (Any): Mongo database handle.
        since (datetime | None): The since (optional, default None).
        sample_limit (int): The sample limit (optional, default 2000).

    Returns:
        dict[str, Any]: The stats.
    """
    if db is None:
        return {"available": False, "reason": "audit database unavailable"}

    query: dict[str, Any] = {}
    if since is not None:
        query["created_at"] = {"$gte": since}
    cursor = (
        db["preflight_stats"]
        .find(query)
        .sort("created_at", -1)
        .limit(sample_limit)
    )
    docs = [doc async for doc in cursor]

    if len(docs) < _MIN_SAMPLE_FOR_ESTIMATES:
        return {
            "available": True,
            "sample_size": len(docs),
            "enough_data": False,
            "message": (
                f"Fewer than {_MIN_SAMPLE_FOR_ESTIMATES} recorded attempts — "
                "not enough data yet to estimate a rate."
            ),
        }

    total = len(docs)
    successes = sum(1 for doc in docs if doc.get("success"))
    by_source: dict[str, dict[str, int]] = {}
    initial_code_counter: Counter[str] = Counter()
    remaining_code_counter: Counter[str] = Counter()
    deterministically_fixed = 0
    llm_fixed = 0

    for doc in docs:
        source = doc.get("source", "unknown")
        bucket = by_source.setdefault(source, {"total": 0, "success": 0})
        bucket["total"] += 1
        if doc.get("success"):
            bucket["success"] += 1
        initial_code_counter.update(doc.get("initial_error_codes") or [])
        remaining_code_counter.update(doc.get("error_codes") or [])
        if doc.get("deterministic_fixes_applied"):
            deterministically_fixed += 1
        if doc.get("llm_attempts"):
            llm_fixed += 1

    return {
        "available": True,
        "sample_size": total,
        "enough_data": True,
        "success_rate": round(successes / total, 4),
        "by_source": {
            source: {
                "total": bucket["total"],
                "success_rate": round(bucket["success"] / bucket["total"], 4),
            }
            for source, bucket in by_source.items()
        },
        # Codes seen before any fix was attempted — this is the honest answer
        # to "what basic/repetitive errors keep showing up", since
        # error_codes alone would undercount anything autofix later resolved.
        "top_recurring_error_codes": initial_code_counter.most_common(10),
        "top_unresolved_error_codes": remaining_code_counter.most_common(10),
        "autofix_resolved_deterministically": deterministically_fixed,
        "autofix_resolved_by_llm": llm_fixed,
    }
