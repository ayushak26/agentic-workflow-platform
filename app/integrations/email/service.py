"""EmailService — connection resolution, idempotency, and the operation switch.

This is the layer the Email node talks to. It exists so that provider-specific
complexity (§49) stays *below* the node contract: the author picks "Send", and
the service is what knows that a send cannot be blindly retried after an
ambiguous provider timeout.

Idempotency, concretely:

    key = sha256(connection, operation, recipients, subject, body, thread)

    ┌─ reserve(key) ──▶ already completed?  ──▶ return the recorded result
    │                                          (deduplicated = true)
    │                  already in flight?   ──▶ refuse; a human resolves it
    └─ not seen        ──▶ record "in flight", call the provider,
                           record the outcome

The reservation is written *before* the provider call, so an ambiguous failure
leaves a durable "we may have sent this" record instead of a silence that the
next retry would turn into a duplicate email to a customer.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.integrations.operations import (
    AmbiguousOperationFailure,
    ExternalOperationLedger,
    OperationInFlight,
    operation_key,
)
from app.observability.logging import get_logger

from .base import (
    SIDE_EFFECT_OPERATIONS,
    EmailAdapter,
    EmailAdapterError,
    EmailAmbiguousFailure,
    EmailConnection,
    EmailDraft,
    EmailOperation,
    EmailResult,
    EmailSearchCriteria,
)

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class EmailConnectionError(EmailAdapterError):
    """The referenced connection does not exist, or is not allowed to do this."""


class EmailOperationInFlight(EmailAdapterError, OperationInFlight):
    """An identical side effect is already in flight or ended ambiguously.

    Inherits the shared OperationInFlight so a caller can catch either the
    email-specific type or the generic one — the MCP integration raises the
    generic form for the same situation against a CRM.
    """


class EmailOperationLedger(ExternalOperationLedger):
    """Email's view of the shared external-operation ledger.

    The reserve-before-acting discipline is not specific to email — creating a
    CRM record has the same duplicate-and-ambiguity problem — so the mechanism
    lives in app/integrations/operations.py and this is the email-scoped
    collection. Keeping one implementation matters because the subtle part (an
    insert that fails on a duplicate id, rather than read-then-write) is easy to
    get wrong twice.
    """

    COLLECTION = "email_operations"

    def __init__(self, db: Any = None):
        super().__init__(db, collection=self.COLLECTION)


class EmailService:
    """One capability, many providers, one place that owns the safety rules."""

    def __init__(
        self,
        adapters: dict[str, EmailAdapter],
        connections: dict[str, EmailConnection],
        ledger: EmailOperationLedger | None = None,
    ):
        self.adapters = adapters
        self.connections = connections
        self.ledger = ledger or EmailOperationLedger()

    # -- connection resolution -----------------------------------------

    def connection(self, connection_id: str) -> EmailConnection:
        found = self.connections.get(connection_id)
        if found is None:
            raise EmailConnectionError(
                f"email connection {connection_id!r} is not configured. "
                f"Available: {sorted(self.connections) or 'none'}"
            )
        return found

    def adapter(self, connection: EmailConnection) -> EmailAdapter:
        found = self.adapters.get(connection.provider)
        if found is None:
            raise EmailConnectionError(
                f"no adapter is registered for provider "
                f"{connection.provider!r}"
            )
        return found

    def describe_connections(self) -> list[dict[str, Any]]:
        """Connection list for the Builder's picker. Credentials are never
        included — the Builder shows a name, not a token."""
        return [
            {
                "id": connection.id,
                "provider": connection.provider,
                "display_name": connection.display_name or connection.id,
                "address": connection.address,
                "allow_send": connection.allow_send,
            }
            for connection in self.connections.values()
        ]

    # -- the operation switch ------------------------------------------

    async def execute(
        self,
        *,
        connection_id: str,
        operation: EmailOperation,
        criteria: EmailSearchCriteria | None = None,
        message_id: str | None = None,
        draft: EmailDraft | None = None,
        idempotency_scope: str | None = None,
    ) -> EmailResult:
        connection = self.connection(connection_id)
        adapter = self.adapter(connection)

        if operation in SIDE_EFFECT_OPERATIONS and not connection.allow_send:
            raise EmailConnectionError(
                f"connection {connection_id!r} is not permitted to "
                f"{operation}. Enable sending on the connection first — a "
                "workflow cannot grant itself that permission."
            )

        if operation == "search":
            messages = await adapter.search(
                connection, criteria or EmailSearchCriteria()
            )
            return EmailResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                messages=messages,
            )

        if operation == "read":
            if not message_id:
                raise EmailAdapterError(
                    "read needs a message_id — map it from a search step"
                )
            message = await adapter.read(connection, message_id)
            return EmailResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                message=message,
                messages=[message],
            )

        if draft is None:
            raise EmailAdapterError(f"{operation} needs a message to work with")

        return await self._perform_write(
            adapter=adapter,
            connection=connection,
            operation=operation,
            draft=draft,
            idempotency_scope=idempotency_scope,
        )

    async def _perform_write(
        self,
        *,
        adapter: EmailAdapter,
        connection: EmailConnection,
        operation: EmailOperation,
        draft: EmailDraft,
        idempotency_scope: str | None,
    ) -> EmailResult:
        key = idempotency_key(
            connection_id=connection.id,
            operation=operation,
            draft=draft,
            scope=idempotency_scope,
        )
        existing = await self.ledger.reserve(
            key,
            {
                "status": "in_flight",
                "connection_id": connection.id,
                "provider": connection.provider,
                "operation": operation,
                "subject": draft.subject[:200],
                "recipients": [address.email for address in draft.to],
                "started_at": _now(),
            },
        )

        if existing is not None:
            status = existing.get("status")
            if status == "completed":
                log.info(
                    "email.deduplicated",
                    operation=operation,
                    connection_id=connection.id,
                    key=key,
                )
                return EmailResult(
                    operation=operation,
                    provider=connection.provider,
                    connection_id=connection.id,
                    draft_id=existing.get("draft_id"),
                    sent_message_id=existing.get("sent_message_id"),
                    deduplicated=True,
                )
            raise EmailOperationInFlight(
                f"an identical {operation} for {[a.email for a in draft.to]} is "
                f"already recorded as {status!r}. It may already have reached "
                "the recipient — check the mailbox before retrying. "
                f"(operation key {key[:12]})"
            )

        try:
            if operation == "create_draft":
                draft_id = await adapter.create_draft(connection, draft)
                await self.ledger.complete(key, {"draft_id": draft_id})
                return EmailResult(
                    operation=operation,
                    provider=connection.provider,
                    connection_id=connection.id,
                    draft_id=draft_id,
                )

            sent_id = (
                await adapter.reply(connection, draft)
                if operation == "reply"
                else await adapter.send(connection, draft)
            )
            await self.ledger.complete(key, {"sent_message_id": sent_id})
            return EmailResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                sent_message_id=sent_id,
            )
        except EmailAmbiguousFailure as error:
            # Keep the reservation. The next attempt is refused with a message
            # that tells a person what to check, rather than sending twice.
            await self.ledger.mark_ambiguous(key, str(error))
            log.error(
                "email.ambiguous_failure",
                operation=operation,
                connection_id=connection.id,
                key=key,
                error=str(error),
            )
            raise
        except Exception as error:
            # Definitive failure: nothing was sent, so free the key.
            await self.ledger.release(key, str(error))
            raise


def idempotency_key(
    *,
    connection_id: str,
    operation: str,
    draft: EmailDraft,
    scope: str | None = None,
) -> str:
    """Stable fingerprint of "this exact email side effect".

    Delegates the hashing to the shared helper and contributes the part that is
    email-specific: which fields identify "the same message". Recipients are
    normalised and sorted so field order and casing cannot change the key.
    """
    return operation_key(
        scope=scope or "",
        target=f"{connection_id}:{operation}",
        payload={
            "to": sorted(address.email.strip().lower() for address in draft.to),
            "cc": sorted(address.email.strip().lower() for address in draft.cc),
            "subject": draft.subject.strip(),
            "body": draft.body_text.strip(),
            "thread": draft.thread_id or draft.in_reply_to_message_id or "",
        },
    )
