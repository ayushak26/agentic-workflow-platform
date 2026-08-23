"""Durable storage for claim-verification results (see
app.evidence.claim_verification). Kept separate from run_history: it stores a
human-facing side-check result, not workflow execution state, and survives
independently of whether the run record itself is later pruned.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_COLLECTION = "claim_verifications"


def _key(run_id: str, record_id: str) -> str:
    """Internal helper for the key step.

    Args:
        run_id (str): Workflow run identifier.
        record_id (str): The record id.

    Returns:
        str: The result.
    """
    return f"{run_id}:{record_id}"


async def ensure_indexes(db) -> None:
    """Ensure the indexes.

    Args:
        db: Mongo database handle.
    """
    col = db[_COLLECTION]
    await col.create_index("key", unique=True)
    await col.create_index("run_id")


async def get_verifications(db, run_id: str) -> dict[str, dict[str, Any]]:
    """Return the verifications.

    Args:
        db: Mongo database handle.
        run_id (str): Workflow run identifier.

    Returns:
        dict[str, dict[str, Any]]: The verifications.
    """
    cursor = db[_COLLECTION].find({"run_id": run_id})
    return {doc["record_id"]: doc["result"] async for doc in cursor}


async def save_verification(
    db,
    run_id: str,
    record_id: str,
    result: dict[str, Any],
) -> None:
    """Save the verification.

    Args:
        db: Mongo database handle.
        run_id (str): Workflow run identifier.
        record_id (str): The record id.
        result (dict[str, Any]): Result mapping.
    """
    await db[_COLLECTION].replace_one(
        {"key": _key(run_id, record_id)},
        {
            "key": _key(run_id, record_id),
            "run_id": run_id,
            "record_id": record_id,
            "result": result,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        upsert=True,
    )
