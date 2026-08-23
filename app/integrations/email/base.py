"""Provider-neutral email contract.

The workflow author sees one Email capability with a provider connection and an
operation. Everything that differs between Gmail and Microsoft Graph — auth,
pagination, MIME encoding, the fact that Gmail threads by `threadId` and Graph by
`conversationId` — lives in an adapter under this contract:

        Email node  (operation selector, references a connection)
              │
        EmailService  (connection resolution, idempotency, audit)
              │
      ┌───────┴────────┐
   Gmail          Microsoft Graph        (+ an in-memory adapter for tests
   adapter        adapter                 and offline demos)

The node never sees an OAuth token: it references a connection id, and the
service resolves credentials. That keeps secrets out of workflow YAML, out of
run history, and out of a workflow exported to a colleague.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EmailOperation = Literal["search", "read", "create_draft", "reply", "send"]

#: Operations that change something outside the platform. These are the ones the
#: autonomy boundary (§48) and the idempotency ledger (§49) exist for.
SIDE_EFFECT_OPERATIONS: frozenset[str] = frozenset({"send", "reply"})

#: Operations that are safe to retry after an ambiguous failure, because
#: repeating them changes nothing. `create_draft` is deliberately *not* here: a
#: retried draft creates a second draft, which is harmless but confusing, so it
#: goes through the same idempotency key as a send.
IDEMPOTENT_OPERATIONS: frozenset[str] = frozenset({"search", "read"})


class EmailAddress(BaseModel):
    """Pydantic model defining the EmailAddress shape.

    Attributes:
        email (str).
        name (str | None).
    """
    model_config = ConfigDict(extra="forbid")

    email: str
    name: str | None = None

    def render(self) -> str:
        """Render the result.

        Returns:
            str: The result.
        """
        return f"{self.name} <{self.email}>" if self.name else self.email


class EmailAttachmentRef(BaseModel):
    """Attachment metadata only. Bytes stay in object storage and are fetched by
    id when needed, the same discipline WorkflowFileRef follows — an attachment
    must never enter LangGraph state or a retry checkpoint."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0


class EmailMessage(BaseModel):
    """One message, in the shape every adapter maps onto."""

    model_config = ConfigDict(extra="forbid")

    id: str
    thread_id: str | None = None
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    from_address: EmailAddress | None = None
    to: list[EmailAddress] = Field(default_factory=list)
    cc: list[EmailAddress] = Field(default_factory=list)
    received_at: datetime | None = None
    is_unread: bool = False
    labels: list[str] = Field(default_factory=list)
    attachments: list[EmailAttachmentRef] = Field(default_factory=list)
    #: Provider-specific extras an author may need but the contract shouldn't
    #: normalise (Gmail's historyId, Graph's internetMessageId).
    provider_fields: dict[str, Any] = Field(default_factory=dict)


class EmailSearchCriteria(BaseModel):
    """Search terms, expressed once and translated per provider."""

    model_config = ConfigDict(extra="forbid")

    query: str = ""
    from_address: str | None = None
    subject_contains: str | None = None
    unread_only: bool = False
    has_attachments: bool = False
    folder: str = "INBOX"
    newer_than_days: int | None = Field(default=None, ge=1, le=3650)
    max_results: int = Field(default=10, ge=1, le=100)


class EmailDraft(BaseModel):
    """An outbound message, before it is sent or saved as a draft."""

    model_config = ConfigDict(extra="forbid")

    to: list[EmailAddress] = Field(default_factory=list)
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    #: Set for reply/create_draft-in-thread so the provider threads correctly.
    in_reply_to_message_id: str | None = None
    thread_id: str | None = None


class EmailResult(BaseModel):
    """What an operation returns. One shape for all five operations, so the
    node's output contract does not change with the operation selector."""

    model_config = ConfigDict(extra="forbid")

    operation: EmailOperation
    provider: str
    connection_id: str
    messages: list[EmailMessage] = Field(default_factory=list)
    message: EmailMessage | None = None
    draft_id: str | None = None
    sent_message_id: str | None = None
    #: True when an idempotency record showed this exact side effect had already
    #: been performed, so the adapter was not called again.
    deduplicated: bool = False
    performed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EmailConnection(BaseModel):
    """A named, credential-bearing connection. Workflow YAML references only
    `id`; the secret material is resolved by the service at run time."""

    model_config = ConfigDict(extra="forbid")

    id: str
    provider: Literal["gmail", "microsoft", "memory"]
    display_name: str = ""
    #: The mailbox this connection acts as. Recorded on every operation so the
    #: audit trail says which mailbox a message was sent from.
    address: str = ""
    #: Resolved from the environment/secret store by the connection registry —
    #: never read from workflow YAML.
    credentials: dict[str, Any] = Field(default_factory=dict, repr=False)
    #: When false, side-effect operations are refused. This is the deployment-
    #: level safety switch: a connection can be usable for reading long before
    #: anyone is willing to let a workflow send from it.
    allow_send: bool = False


class EmailAdapterError(RuntimeError):
    """A provider call failed in a way the caller can describe to a user."""


class EmailAmbiguousFailure(EmailAdapterError):
    """A side-effect call failed without a definitive outcome — a timeout, a
    dropped connection, a 5xx after the request was accepted.

    Raised separately because it is the one case where retrying is genuinely
    unsafe: the message may already have gone out. The service records the
    attempt against its idempotency key before calling the adapter precisely so
    this case can be resolved rather than guessed at.
    """


class EmailAdapter(ABC):
    """What every provider implementation must offer.

    Five methods, matching the five operations the node exposes. A provider that
    cannot do one raises EmailAdapterError rather than silently no-op'ing.
    """

    provider: str

    @abstractmethod
    async def search(
        self, connection: EmailConnection, criteria: EmailSearchCriteria
    ) -> list[EmailMessage]:
        """Search the result.

        Args:
            connection (EmailConnection): The connection.
            criteria (EmailSearchCriteria): The criteria.

        Returns:
            list[EmailMessage]: The result.
        """
        ...

    @abstractmethod
    async def read(
        self, connection: EmailConnection, message_id: str
    ) -> EmailMessage:
        """Read the result.

        Args:
            connection (EmailConnection): The connection.
            message_id (str): The message id.

        Returns:
            EmailMessage: The result.
        """
        ...

    @abstractmethod
    async def create_draft(
        self, connection: EmailConnection, draft: EmailDraft
    ) -> str:
        """Returns the provider's draft id."""

    @abstractmethod
    async def send(
        self, connection: EmailConnection, draft: EmailDraft
    ) -> str:
        """Returns the provider's sent-message id."""

    async def reply(
        self, connection: EmailConnection, draft: EmailDraft
    ) -> str:
        """Reply defaults to a threaded send.

        Providers with a dedicated reply endpoint (Graph's /reply) override this
        to let the provider quote and thread the original; the default is correct
        for any provider where threading is carried by headers.
        """
        if not draft.in_reply_to_message_id and not draft.thread_id:
            raise EmailAdapterError(
                "reply needs the message being replied to; map "
                "in_reply_to_message_id from the read/search step"
            )
        return await self.send(connection, draft)
