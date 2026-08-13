"""In-memory email adapter.

Two real jobs, neither of them "a stub we forgot to replace":

1.  Tests exercise the *same* EmailService code path — connection resolution,
    permission check, idempotency ledger — that Gmail and Graph go through.
2.  A workflow can be built and demonstrated end to end before anyone has
    connected a real mailbox, which matters when the point of the demo is the
    business logic rather than the OAuth flow.

It is explicitly not a mock: it stores messages, threads replies, and honours
`allow_send`, so behaviour observed here is behaviour the contract guarantees.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from .base import (
    EmailAdapter,
    EmailAdapterError,
    EmailConnection,
    EmailDraft,
    EmailMessage,
    EmailSearchCriteria,
)


class InMemoryEmailAdapter(EmailAdapter):
    provider = "memory"

    def __init__(self, messages: list[EmailMessage] | None = None):
        self.messages: dict[str, EmailMessage] = {
            message.id: message for message in (messages or [])
        }
        self.drafts: dict[str, EmailDraft] = {}
        self.sent: list[EmailDraft] = []

    def add(self, message: EmailMessage) -> EmailMessage:
        self.messages[message.id] = message
        return message

    async def search(
        self, connection: EmailConnection, criteria: EmailSearchCriteria
    ) -> list[EmailMessage]:
        del connection
        cutoff = (
            datetime.now(UTC) - timedelta(days=criteria.newer_than_days)
            if criteria.newer_than_days
            else None
        )
        found: list[EmailMessage] = []
        for message in self.messages.values():
            if criteria.unread_only and not message.is_unread:
                continue
            if criteria.has_attachments and not message.attachments:
                continue
            if criteria.from_address and (
                not message.from_address
                or criteria.from_address.lower()
                not in message.from_address.email.lower()
            ):
                continue
            if (
                criteria.subject_contains
                and criteria.subject_contains.lower() not in message.subject.lower()
            ):
                continue
            if criteria.query and criteria.query.lower() not in (
                f"{message.subject}\n{message.body_text}".lower()
            ):
                continue
            if cutoff and message.received_at and message.received_at < cutoff:
                continue
            found.append(message)

        found.sort(
            key=lambda item: item.received_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return found[: criteria.max_results]

    async def read(
        self, connection: EmailConnection, message_id: str
    ) -> EmailMessage:
        del connection
        message = self.messages.get(message_id)
        if message is None:
            raise EmailAdapterError(f"no message with id {message_id!r}")
        return message

    async def create_draft(
        self, connection: EmailConnection, draft: EmailDraft
    ) -> str:
        del connection
        draft_id = f"draft-{uuid.uuid4().hex[:12]}"
        self.drafts[draft_id] = draft
        return draft_id

    async def send(self, connection: EmailConnection, draft: EmailDraft) -> str:
        if not draft.to:
            raise EmailAdapterError("send needs at least one recipient")
        sent_id = f"sent-{uuid.uuid4().hex[:12]}"
        self.sent.append(draft)
        self.messages[sent_id] = EmailMessage(
            id=sent_id,
            thread_id=draft.thread_id or sent_id,
            subject=draft.subject,
            body_text=draft.body_text,
            body_html=draft.body_html,
            from_address=None,
            to=list(draft.to),
            cc=list(draft.cc),
            received_at=datetime.now(UTC),
            labels=["SENT"],
            provider_fields={"connection": connection.id},
        )
        return sent_id
