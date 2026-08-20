"""Subprocess launch correlation — the webhook side of the Subprocess node.

A Subprocess step (app/nodes/subprocess.py) launches its referenced workflow
as a fully independent run and then pauses. Nothing links that running child
back to its waiting parent except this collection: one document per launch,
created when the child is launched, looked up by the parent's own launch key
whenever the SubprocessAgent node function re-runs, looked up again by the
child's own run_id once that child finishes (so the child's completely
ordinary finalize path — the same one every run goes through, see
app.workflow.orchestration — can find who is waiting for it), and delivered
exactly once via its single-use callback token.

The delivery step deliberately does NOT delete the document (an earlier
design did, mirroring app/api/email_oauth.py's own `find_one_and_delete`
pattern for its pending-state collection — and it was a real bug: resuming
the parent re-runs SubprocessAgent.run() from the top exactly like a fresh
launch would, so by the time that re-entry happened, the just-deleted record
no longer stopped it from launching a *second* child). Instead, delivery
atomically flips `status` from "pending" to "delivered" and stores the
decision on the same document — so a re-entry finds a record in either
state and knows exactly what to do: "pending" means the child is still
running (re-pause), "delivered" means the result is already sitting right
here (skip pausing again entirely). The single-use guarantee comes from that
status-guarded transition, not from deletion, and a retried or duplicate
delivery attempt simply matches nothing and is a safe no-op.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any


_COLLECTION = "subprocess_launches"
# Slack added on top of the node's own configured wait timeout, so the
# correlation record outlives the parent's own patience — an abandoned or
# never-called-back launch still ages out of Mongo on its own rather than
# lingering forever, without racing the node's own timeout handling.
_RECORD_TTL_SLACK = timedelta(hours=1)


async def ensure_indexes(db: Any) -> None:
    await db[_COLLECTION].create_index("token", unique=True)
    await db[_COLLECTION].create_index("child_run_id", unique=True)
    await db[_COLLECTION].create_index([("expires_at", 1)], expireAfterSeconds=0)

    # Historical leftover: an earlier version of this collection stored the
    # launch key in its own `launch_key` field and uniquely indexed it. Once
    # `reserve_launch` switched to using `_id` as the key directly, that
    # field stopped being written — but on any database that already had the
    # old unique index, every document now indexes as `launch_key: null`,
    # so a *second* subprocess launch anywhere in the whole collection
    # collides with the first and gets DuplicateKeyError'd into oblivion
    # (`reserve_launch`'s own not-created fallback then can't find it by
    # `_id` either, since the collision was never really about `_id`).
    # Dropping it is safe and idempotent: nothing reads or writes
    # `launch_key` anymore, and a deployment that never had this index
    # (the normal case) just gets a harmless "index not found".
    try:
        await db[_COLLECTION].drop_index("launch_key_1")
    except Exception:
        pass


async def find_by_launch_key(db: Any, launch_key: str) -> dict[str, Any] | None:
    return await db[_COLLECTION].find_one({"_id": launch_key})


async def reserve_launch(
    db: Any,
    *,
    launch_key: str,
    parent_run_id: str,
    parent_node_id: str,
    parent_session_id: str,
    child_run_id: str,
    child_workflow: str,
    result_from: str,
    result_node: str | None,
    timeout_seconds: float,
) -> tuple[dict[str, Any], bool]:
    """Claim `launch_key` for one child launch. Returns (doc, created).

    `launch_key` is the document's own `_id` — an insert that fails on a
    duplicate id, not a read-then-write: two racing entries into the same
    SubprocessAgent node must not both see "nothing launched yet" and both
    start a child. The callback URL uses a separate, unguessable `token`
    field instead of this deterministic id, since a `parent_run_id:node_id`
    key is not something an external caller should be able to guess or
    enumerate.
    """
    now = datetime.now(UTC)
    doc = {
        "_id": launch_key,
        "token": secrets.token_urlsafe(32),
        "parent_run_id": parent_run_id,
        "parent_node_id": parent_node_id,
        "parent_session_id": parent_session_id,
        "child_run_id": child_run_id,
        "child_workflow": child_workflow,
        "result_from": result_from,
        "result_node": result_node,
        "status": "pending",
        "delivered_decision": None,
        "created_at": now,
        "expires_at": now + timedelta(seconds=timeout_seconds) + _RECORD_TTL_SLACK,
    }
    from pymongo.errors import DuplicateKeyError

    try:
        await db[_COLLECTION].insert_one(doc)
        return doc, True
    except DuplicateKeyError:
        existing = await db[_COLLECTION].find_one({"_id": launch_key})
        return existing, False


async def find_by_child_run_id(db: Any, child_run_id: str) -> dict[str, Any] | None:
    return await db[_COLLECTION].find_one({"child_run_id": child_run_id})


async def find_by_token(db: Any, token: str) -> dict[str, Any] | None:
    return await db[_COLLECTION].find_one({"token": token})


async def deliver(db: Any, *, token: str, decision: dict[str, Any]) -> bool:
    """Atomically mark the launch "delivered", once.

    Returns False (a no-op) when nothing matched — already delivered, or the
    token never existed — exactly like app/api/email_oauth.py's
    `find_one_and_delete` returning None for an already-consumed state, just
    expressed as a status transition instead of a deletion (see module
    docstring for why the deletion approach was a real bug here).
    """
    result = await db[_COLLECTION].update_one(
        {"token": token, "status": "pending"},
        {"$set": {"status": "delivered", "delivered_decision": decision}},
    )
    return getattr(result, "matched_count", 0) == 1
