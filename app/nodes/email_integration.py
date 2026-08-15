"""EmailAgent — one email capability with an operation selector.

This one node replaces the shape a platform usually grows into:

    GmailSearchAgent   GmailReadAgent   GmailSendAgent
    OutlookSearchAgent OutlookReadAgent OutlookSendAgent   …

The author picks a connection and an operation. Provider differences live in
adapters beneath the node contract (app/integrations/email/), and the safety
rules that differ per operation — a `read` is freely retryable, a `send` after an
ambiguous provider timeout is not — live in EmailService.

The workflow references a *connection id*. It never contains a token, so a
workflow can be exported, versioned and shared without leaking mailbox access.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.integrations.email import (
    EmailAddress,
    EmailDraft,
    EmailSearchCriteria,
)
from app.integrations.email.base import SIDE_EFFECT_OPERATIONS
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


EmailOperationName = Literal["search", "read", "create_draft", "reply", "send"]

OPERATION_PRESETS: list[dict[str, Any]] = [
    {
        "id": "search",
        "label": "Search",
        "summary": "Find messages matching criteria. Read-only.",
        "config": {"operation": "search"},
        "external_action": False,
    },
    {
        "id": "read",
        "label": "Read",
        "summary": "Fetch one message in full, including attachment metadata.",
        "config": {"operation": "read"},
        "external_action": False,
    },
    {
        "id": "create_draft",
        "label": "Create Draft",
        "summary": "Save a draft for a person to review and send. Nothing leaves the building.",
        "config": {"operation": "create_draft"},
        "external_action": True,
    },
    {
        "id": "reply",
        "label": "Reply",
        "summary": "Reply in the original thread. Sends immediately.",
        "config": {"operation": "reply"},
        "external_action": True,
    },
    {
        "id": "send",
        "label": "Send",
        "summary": "Send a new message immediately. Put a human review in front of this.",
        "config": {"operation": "send"},
        "external_action": True,
    },
]


class EmailRecipient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    name: str | None = None


class EmailNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Named connection resolved by EmailService. Never a token.
    connection: str = Field(description="Which configured mailbox connection this step acts against.")
    operation: EmailOperationName = Field(
        default="search",
        description="The email operation to perform: search/read change nothing; create_draft/reply/send take action.",
    )

    # search
    query: str = Field(default="", description="Search query, in search operations.")
    from_address: str | None = Field(default=None, description="Filter search results to this sender.")
    subject_contains: str | None = Field(default=None, description="Filter search results to a subject substring.")
    unread_only: bool = Field(default=False, description="Only match unread messages, in search operations.")
    has_attachments: bool = Field(default=False, description="Only match messages with attachments, in search operations.")
    folder: str = Field(default="inbox", description="Mailbox folder to search.")
    newer_than_days: int | None = Field(default=None, ge=1, le=3650, description="Only match messages newer than this many days.")
    max_results: int = Field(default=10, ge=1, le=100, description="Maximum number of search results to return.")

    # read / reply
    message_id: str | None = Field(default=None, description="Message to read or reply to.")
    thread_id: str | None = Field(default=None, description="Thread to reply within.")

    # create_draft / reply / send
    to: list[EmailRecipient] = Field(default_factory=list, description="Recipients, in create_draft/reply/send operations.")
    cc: list[EmailRecipient] = Field(default_factory=list, description="CC recipients.")
    bcc: list[EmailRecipient] = Field(default_factory=list, description="BCC recipients.")
    subject: str = Field(default="", description="Message subject, in create_draft/send operations.")
    body: str = Field(default="", description="Plain-text message body — normally a template reference.")
    body_html: str | None = Field(default=None, description="Optional HTML message body.")

    @model_validator(mode="after")
    def operation_has_what_it_needs(self) -> "EmailNodeConfig":
        # Templated values are substituted before run(), so a config that maps
        # `message_id: "{{outputs.find.messages}}"` looks present here and is
        # validated on its real value at runtime. This checks the author
        # supplied *something*, which is the mistake worth catching in the
        # Builder.
        if self.operation == "read" and not self.message_id:
            raise ValueError(
                "read needs a message_id — map it from a search step"
            )
        if self.operation == "reply" and not (
            self.message_id or self.thread_id
        ):
            raise ValueError(
                "reply needs the message or thread being replied to"
            )
        if self.operation in ("create_draft", "send") and not self.to:
            raise ValueError(f"{self.operation} needs at least one recipient")
        if self.operation in ("create_draft", "reply", "send") and not (
            self.body or self.body_html
        ):
            raise ValueError(f"{self.operation} needs a message body")
        return self


class EmailNodeInput(BaseModel):
    pass


class EmailMessageSummary(BaseModel):
    """The message shape downstream nodes address.

    Flattened from the integration's EmailMessage so a mapping reads
    `messages.items.subject` rather than a nested address object — and so
    attachment bytes can never appear in workflow state.
    """

    id: str
    thread_id: str | None = None
    subject: str = ""
    body: str = ""
    from_email: str | None = None
    from_name: str | None = None
    to: list[str] = Field(default_factory=list)
    received_at: str | None = None
    is_unread: bool = False
    attachment_count: int = 0
    attachment_names: list[str] = Field(default_factory=list)


class EmailNodeOutput(BaseModel):
    operation: str
    provider: str = ""
    connection: str = ""
    messages: list[EmailMessageSummary] = Field(default_factory=list)
    message: EmailMessageSummary | None = None
    message_count: int = 0
    draft_id: str | None = None
    sent_message_id: str | None = None
    #: True when the idempotency ledger showed this exact side effect had already
    #: happened, so nothing was sent twice.
    deduplicated: bool = False


@NodeRegistry.register
class EmailAgent(NodeType):
    type_name = "EmailAgent"
    description = (
        "Email in one capability: search, read, draft, reply or send, over "
        "Gmail or Microsoft — provider differences live in adapters."
    )
    input_schema = EmailNodeInput
    output_schema = EmailNodeOutput
    config_schema = EmailNodeConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "external"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Performs one email operation against a configured mailbox "
            "connection. Search and read change nothing; draft, reply and send "
            "act outside the platform."
        ),
        "why": (
            "One capability with an operation selector, instead of a node type "
            "per provider and verb. Which mailbox and which credentials is a "
            "deployment decision, not workflow content."
        ),
        "receives": "Search criteria, a message id, or a drafted reply — usually mapped from upstream nodes.",
        "produces": "messages / message for reads; draft_id or sent_message_id for writes.",
        "uses_ai": False,
        "external_action": True,
        "presets": OPERATION_PRESETS,
        "safety": (
            "Sending is refused unless the connection permits it. A send that "
            "fails ambiguously is recorded, not retried — a person confirms "
            "whether it went out."
        ),
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"email"}

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        """Authorise the message shape as dotted paths.

        `messages.items.<field>` follows the same convention field_schema uses
        for a list of objects, so mapping a search result reads the same as
        mapping an AI Task's list field.
        """
        declared = set(EmailNodeOutput.model_fields)
        summary_fields = set(EmailMessageSummary.model_fields)
        return (
            declared
            | {f"message.{name}" for name in summary_fields}
            | {f"messages.items.{name}" for name in summary_fields}
        )

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = EmailNodeConfig(**resolved_config)
        service = self.services.get("email")
        if service is None:
            raise RuntimeError(
                f"EmailAgent '{self.node_id}' needs the email service. Configure "
                "at least one email connection in the deployment."
            )

        # The run id scopes the idempotency key: retrying one run must not send
        # a second copy, while two different runs replying to two customers must
        # not be mistaken for duplicates of each other.
        run_id = (state.get("inputs") or {}).get("SYSTEM.run_id") or "unknown"

        result = await service.execute(
            connection_id=cfg.connection,
            operation=cfg.operation,
            criteria=_criteria(cfg) if cfg.operation == "search" else None,
            message_id=cfg.message_id if cfg.operation == "read" else None,
            draft=_draft(cfg) if cfg.operation in ("create_draft", "reply", "send") else None,
            idempotency_scope=f"{run_id}:{self.node_id}",
        )

        summaries = [_summarise(message) for message in result.messages]
        single = _summarise(result.message) if result.message else None
        return {
            "operation": result.operation,
            "provider": result.provider,
            "connection": result.connection_id,
            "messages": [item.model_dump() for item in summaries],
            "message": single.model_dump() if single else None,
            "message_count": len(summaries),
            "draft_id": result.draft_id,
            "sent_message_id": result.sent_message_id,
            "deduplicated": result.deduplicated,
        }


def _criteria(cfg: EmailNodeConfig) -> EmailSearchCriteria:
    return EmailSearchCriteria(
        query=cfg.query,
        from_address=cfg.from_address,
        subject_contains=cfg.subject_contains,
        unread_only=cfg.unread_only,
        has_attachments=cfg.has_attachments,
        folder=cfg.folder,
        newer_than_days=cfg.newer_than_days,
        max_results=cfg.max_results,
    )


def _draft(cfg: EmailNodeConfig) -> EmailDraft:
    return EmailDraft(
        to=[EmailAddress(email=item.email, name=item.name) for item in cfg.to],
        cc=[EmailAddress(email=item.email, name=item.name) for item in cfg.cc],
        bcc=[EmailAddress(email=item.email, name=item.name) for item in cfg.bcc],
        subject=cfg.subject,
        body_text=cfg.body,
        body_html=cfg.body_html,
        in_reply_to_message_id=cfg.message_id,
        thread_id=cfg.thread_id,
    )


def _summarise(message: Any) -> EmailMessageSummary:
    return EmailMessageSummary(
        id=message.id,
        thread_id=message.thread_id,
        subject=message.subject,
        body=message.body_text,
        from_email=message.from_address.email if message.from_address else None,
        from_name=message.from_address.name if message.from_address else None,
        to=[address.email for address in message.to],
        received_at=(
            message.received_at.isoformat() if message.received_at else None
        ),
        is_unread=message.is_unread,
        attachment_count=len(message.attachments),
        attachment_names=[item.name for item in message.attachments],
    )


def is_side_effect(operation: str) -> bool:
    """Whether this operation changes something outside the platform.

    Read by preflight's EXTERNAL_ACTION_WITHOUT_REVIEW check, so the node and
    the check cannot disagree about which operations are consequential.
    """
    return operation in SIDE_EFFECT_OPERATIONS or operation == "create_draft"
