"""Durable record of attempted external side effects.

Sending an email and creating a CRM lead are the same problem wearing different
clothes: an action outside the platform that a retry must not repeat, and whose
outcome is sometimes genuinely unknown. Both go through this ledger.

    reserve(key) ──▶ already completed? ──▶ replay the recorded outcome
                 ──▶ already in flight?  ──▶ refuse; a person reconciles
                 ──▶ not seen            ──▶ record "in flight", act,
                                             record the outcome

The reservation is written **before** the external call. That ordering is the
whole design: an ambiguous failure then leaves a durable "this may have
happened" record instead of a silence that the next retry turns into a duplicate
email to a customer, or a duplicate opportunity in their CRM.

Backed by Mongo when the platform has a database, and by a process-local dict
otherwise — the fallback is honest about its limits: it protects against a retry
inside one process, not across a restart.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

log = get_logger(__name__)


class OperationInFlight(RuntimeError):
    """An identical side effect is already in flight, or ended ambiguously.

    Deliberately not retryable by the platform. The correct resolution is a
    person checking the target system, which is exactly the situation this
    ledger exists to make visible rather than silently duplicate.
    """


class AmbiguousOperationFailure(RuntimeError):
    """An external call failed without a definitive outcome.

    A timeout, a dropped connection, a 5xx after the request was accepted. The
    reservation is kept so the next attempt is refused rather than guessed at.
    """


def _now() -> datetime:
    """Internal helper for the now step.

    Returns:
        datetime: The result.
    """
    return datetime.now(UTC)


class ExternalOperationLedger:
    """Provides the ExternalOperationLedger behaviour."""
    def __init__(self, db: Any = None, *, collection: str = "external_operations"):
        """Initialize the ExternalOperationLedger.

        Args:
            db (Any): Mongo database handle (optional, default None).
            collection (str): Mongo collection (optional, default 'external_operations').
        """
        self.db = db
        self.collection = collection
        self._local: dict[str, dict[str, Any]] = {}

    async def find(self, key: str) -> dict[str, Any] | None:
        """Find the result.

        Args:
            key (str): Lookup key.

        Returns:
            dict[str, Any] | None: The result.
        """
        if self.db is None:
            return self._local.get(key)
        return await self.db[self.collection].find_one({"_id": key})

    async def reserve(self, key: str, record: dict[str, Any]) -> dict[str, Any] | None:
        """Claim the key. Returns the existing record if one is already there.

        An insert that fails on a duplicate id, not a read-then-write: two
        parallel branches performing the same action must not both see "nothing
        recorded" and both proceed.
        """
        if self.db is None:
            existing = self._local.get(key)
            if existing is not None:
                return existing
            self._local[key] = {**record, "reserved_at": _now()}
            return None

        from pymongo.errors import DuplicateKeyError

        try:
            await self.db[self.collection].insert_one(
                {"_id": key, **record, "reserved_at": _now()}
            )
        except DuplicateKeyError:
            return await self.db[self.collection].find_one({"_id": key})
        return None

    async def complete(self, key: str, outcome: dict[str, Any]) -> None:
        """Complete the result.

        Args:
            key (str): Lookup key.
            outcome (dict[str, Any]): The outcome.
        """
        patch = {"status": "completed", "completed_at": _now(), **outcome}
        if self.db is None:
            self._local.setdefault(key, {}).update(patch)
            return
        await self.db[self.collection].update_one({"_id": key}, {"$set": patch})

    async def mark_ambiguous(self, key: str, error: str) -> None:
        """Mark the ambiguous.

        Args:
            key (str): Lookup key.
            error (str): Error value or message.
        """
        patch = {"status": "ambiguous", "error": error[:500], "failed_at": _now()}
        if self.db is None:
            self._local.setdefault(key, {}).update(patch)
            return
        await self.db[self.collection].update_one({"_id": key}, {"$set": patch})

    async def release(self, key: str, error: str) -> None:
        """Drop the reservation after a *definitive* failure.

        A target that rejected the request (bad address, invalid field, auth)
        did nothing, so the key must not stay claimed — otherwise fixing the
        input and re-running would be refused as a duplicate.
        """
        del error
        if self.db is None:
            self._local.pop(key, None)
            return
        await self.db[self.collection].delete_one({"_id": key})


def operation_key(*, scope: str, target: str, payload: Any) -> str:
    """Stable fingerprint of "this exact side effect".

    `scope` is normally `run_id:node_id`, which is what makes a *retry of one
    run* deduplicate while two genuinely separate runs performing the same
    action do not collide. The payload is canonicalised (sorted keys) so field
    ordering cannot change the key.
    """
    encoded = json.dumps(
        {"scope": scope, "target": target, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
