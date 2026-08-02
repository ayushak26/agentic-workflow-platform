"""Durable storage for "Ask AI about this run" conversations.

One document per (run_id, session_id) holding the full turn history, mirroring
how run_checkpoints sits alongside run_history — a small, focused collection
rather than another field bolted onto the run_history document.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _require_session(session_id: str) -> str:
    if not session_id or not session_id.strip():
        raise ValueError("session_id is mandatory for run chat access")
    return session_id


async def ensure_run_chat_indexes(db) -> None:
    await db["run_chats"].create_index(
        [("session_id", 1), ("run_id", 1)], unique=True
    )


async def get_run_chat_turns(db, *, session_id: str, run_id: str) -> list[dict[str, Any]]:
    _require_session(session_id)
    doc = await db["run_chats"].find_one(
        {"session_id": session_id, "run_id": run_id}, {"_id": 0},
    )
    return (doc or {}).get("turns", [])


async def append_run_chat_turn(
    db, *, session_id: str, run_id: str, question: str, answer: str, model: str,
) -> list[dict[str, Any]]:
    """Append one Q&A turn and return the full updated turn list."""
    _require_session(session_id)
    now = datetime.now(timezone.utc)
    turns = [
        {"role": "user", "content": question, "ts": now.timestamp()},
        {"role": "assistant", "content": answer, "model": model, "ts": now.timestamp()},
    ]
    await db["run_chats"].update_one(
        {"session_id": session_id, "run_id": run_id},
        {
            "$push": {"turns": {"$each": turns}},
            "$set": {"updated_at": now},
            "$setOnInsert": {
                "session_id": session_id,
                "run_id": run_id,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return await get_run_chat_turns(db, session_id=session_id, run_id=run_id)
