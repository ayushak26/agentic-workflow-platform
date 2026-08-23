"""Gmail adapter — Gmail API v1 over the shared httpx client.

Everything Gmail-specific is confined here: the `q` search grammar, base64url
RFC-2822 bodies, `threadId` threading, and the payload/parts tree that a message
body has to be dug out of. The Email node above sees none of it.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime
from email.message import EmailMessage as MIMEMessage
from email.utils import parseaddr
from typing import Any

import httpx

from app.observability.logging import get_logger

from .base import (
    EmailAdapter,
    EmailAdapterError,
    EmailAmbiguousFailure,
    EmailAttachmentRef,
    EmailAddress,
    EmailConnection,
    EmailDraft,
    EmailMessage,
    EmailSearchCriteria,
)

log = get_logger(__name__)

_API = "https://gmail.googleapis.com/gmail/v1"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class GmailAdapter(EmailAdapter):
    """Provides the GmailAdapter behaviour."""
    provider = "gmail"

    def __init__(self, client: httpx.AsyncClient | None = None):
        # An injected client is how tests drive this adapter against a transport
        # mock, and how a deployment shares one connection pool.
        """Initialize the GmailAdapter.

        Args:
            client (httpx.AsyncClient | None): Client instance (optional, default None).
        """
        self._client = client

    # -- transport ------------------------------------------------------

    def _headers(self, connection: EmailConnection) -> dict[str, str]:
        """Internal helper for the headers step.

        Args:
            connection (EmailConnection): The connection.

        Returns:
            dict[str, str]: The result.
        """
        token = connection.credentials.get("access_token")
        if not token:
            raise EmailAdapterError(
                f"gmail connection {connection.id!r} has no access_token. "
                "Connections are resolved from the secret store, not from "
                "workflow YAML."
            )
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        connection: EmailConnection,
        method: str,
        path: str,
        *,
        side_effect: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Internal helper for the request step.

        Args:
            connection (EmailConnection): The connection.
            method (str): The method.
            path (str): Filesystem path.
            side_effect (bool): The side effect (optional, default False).
            **kwargs (Any): Keyword arguments.

        Returns:
            dict[str, Any]: The result.
        """
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT)
        owns_client = self._client is None
        try:
            response = await client.request(
                method,
                f"{_API}{path}",
                headers=self._headers(connection),
                **kwargs,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            # A read timeout on a send means the request may well have been
            # accepted. Distinguishing that from a definitive rejection is the
            # whole point of the ambiguous-failure type: the service keeps the
            # idempotency reservation instead of allowing a duplicate send.
            if side_effect:
                raise EmailAmbiguousFailure(
                    f"gmail {method} {path} did not return a definitive result: "
                    f"{error}"
                ) from error
            raise EmailAdapterError(f"gmail {method} {path} failed: {error}") from error
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 500 and side_effect:
            raise EmailAmbiguousFailure(
                f"gmail {method} {path} returned {response.status_code}; the "
                "outcome is unknown"
            )
        if response.status_code >= 400:
            raise EmailAdapterError(
                f"gmail {method} {path} returned {response.status_code}: "
                f"{response.text[:300]}"
            )
        return response.json() if response.content else {}

    # -- operations -----------------------------------------------------

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
        listing = await self._request(
            connection,
            "GET",
            "/users/me/messages",
            params={
                "q": _gmail_query(criteria),
                "maxResults": criteria.max_results,
            },
        )
        ids = [item["id"] for item in listing.get("messages", [])]
        # Gmail's list endpoint returns ids only, so each message is fetched.
        # Sequential rather than gathered: a search of 100 in parallel is the
        # fastest way to hit a per-user rate limit, and the node's max_results
        # is capped at 100 for the same reason.
        return [await self.read(connection, message_id) for message_id in ids]

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
        raw = await self._request(
            connection,
            "GET",
            f"/users/me/messages/{message_id}",
            params={"format": "full"},
        )
        return _to_message(raw)

    async def create_draft(
        self, connection: EmailConnection, draft: EmailDraft
    ) -> str:
        """Create the draft.

        Args:
            connection (EmailConnection): The connection.
            draft (EmailDraft): The draft.

        Returns:
            str: The draft.
        """
        payload: dict[str, Any] = {"message": {"raw": _encode(draft, connection)}}
        if draft.thread_id:
            payload["message"]["threadId"] = draft.thread_id
        created = await self._request(
            connection,
            "POST",
            "/users/me/drafts",
            side_effect=True,
            json=payload,
        )
        return str(created.get("id") or "")

    async def send(self, connection: EmailConnection, draft: EmailDraft) -> str:
        """Send the result.

        Args:
            connection (EmailConnection): The connection.
            draft (EmailDraft): The draft.

        Returns:
            str: The result.
        """
        payload: dict[str, Any] = {"raw": _encode(draft, connection)}
        if draft.thread_id:
            payload["threadId"] = draft.thread_id
        sent = await self._request(
            connection,
            "POST",
            "/users/me/messages/send",
            side_effect=True,
            json=payload,
        )
        return str(sent.get("id") or "")


def _gmail_query(criteria: EmailSearchCriteria) -> str:
    """Translate neutral criteria into Gmail's `q` grammar."""
    parts: list[str] = []
    if criteria.query:
        parts.append(criteria.query)
    if criteria.from_address:
        parts.append(f"from:{criteria.from_address}")
    if criteria.subject_contains:
        parts.append(f'subject:"{criteria.subject_contains}"')
    if criteria.unread_only:
        parts.append("is:unread")
    if criteria.has_attachments:
        parts.append("has:attachment")
    if criteria.newer_than_days:
        parts.append(f"newer_than:{criteria.newer_than_days}d")
    if criteria.folder and criteria.folder.upper() != "INBOX":
        parts.append(f"in:{criteria.folder}")
    else:
        parts.append("in:inbox")
    return " ".join(parts)


def _encode(draft: EmailDraft, connection: EmailConnection) -> str:
    """Build a base64url RFC-2822 message, which is what Gmail accepts."""
    message = MIMEMessage()
    message["To"] = ", ".join(address.render() for address in draft.to)
    if draft.cc:
        message["Cc"] = ", ".join(address.render() for address in draft.cc)
    if draft.bcc:
        message["Bcc"] = ", ".join(address.render() for address in draft.bcc)
    if connection.address:
        message["From"] = connection.address
    message["Subject"] = draft.subject
    if draft.in_reply_to_message_id:
        # Both headers: In-Reply-To threads the conversation for the recipient's
        # client, References keeps the whole chain intact for clients that show
        # it. Gmail's own threadId only affects the sender's mailbox.
        message["In-Reply-To"] = draft.in_reply_to_message_id
        message["References"] = draft.in_reply_to_message_id
    message.set_content(draft.body_text or "")
    if draft.body_html:
        message.add_alternative(draft.body_html, subtype="html")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _to_message(raw: dict[str, Any]) -> EmailMessage:
    """Internal helper for the to message step.

    Args:
        raw (dict[str, Any]): Raw value.

    Returns:
        EmailMessage: The message.
    """
    payload = raw.get("payload") or {}
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in payload.get("headers", [])
    }
    body_text, body_html = _extract_bodies(payload)
    return EmailMessage(
        id=str(raw.get("id") or ""),
        thread_id=raw.get("threadId"),
        subject=headers.get("subject", ""),
        body_text=body_text or raw.get("snippet", ""),
        body_html=body_html,
        from_address=_parse_address(headers.get("from")),
        to=_parse_addresses(headers.get("to")),
        cc=_parse_addresses(headers.get("cc")),
        received_at=_parse_internal_date(raw.get("internalDate")),
        is_unread="UNREAD" in (raw.get("labelIds") or []),
        labels=list(raw.get("labelIds") or []),
        attachments=_extract_attachments(payload),
        provider_fields={
            "history_id": raw.get("historyId"),
            "message_id_header": headers.get("message-id"),
        },
    )


def _extract_bodies(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Walk the MIME parts tree for text and HTML bodies.

    Gmail nests parts arbitrarily (multipart/alternative inside
    multipart/mixed when there are attachments), so this recurses rather than
    assuming the two-part shape that simple messages happen to have.
    """
    text: str = ""
    html: str | None = None

    def walk(part: dict[str, Any]) -> None:
        """Compute the walk.

        Args:
            part (dict[str, Any]): The part.
        """
        nonlocal text, html
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data:
            decoded = _decode_b64(data)
            if mime == "text/plain" and not text:
                text = decoded
            elif mime == "text/html" and html is None:
                html = decoded
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return text, html


def _extract_attachments(payload: dict[str, Any]) -> list[EmailAttachmentRef]:
    """Extract the attachments.

    Args:
        payload (dict[str, Any]): Event or audit payload.

    Returns:
        list[EmailAttachmentRef]: The attachments.
    """
    found: list[EmailAttachmentRef] = []

    def walk(part: dict[str, Any]) -> None:
        """Compute the walk.

        Args:
            part (dict[str, Any]): The part.
        """
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        if attachment_id:
            found.append(
                EmailAttachmentRef(
                    id=str(attachment_id),
                    name=part.get("filename") or "attachment",
                    content_type=part.get("mimeType") or "application/octet-stream",
                    size_bytes=int(body.get("size") or 0),
                )
            )
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return found


def _decode_b64(data: str) -> str:
    """Decode the b64.

    Args:
        data (str): Data mapping.

    Returns:
        str: The b64.
    """
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _parse_address(value: str | None) -> EmailAddress | None:
    """Parse the address.

    Args:
        value (str | None): Value to process.

    Returns:
        EmailAddress | None: The address.
    """
    if not value:
        return None
    name, email = parseaddr(value)
    if not email:
        return None
    return EmailAddress(email=email, name=name or None)


def _parse_addresses(value: str | None) -> list[EmailAddress]:
    """Parse the addresses.

    Args:
        value (str | None): Value to process.

    Returns:
        list[EmailAddress]: The addresses.
    """
    if not value:
        return []
    parsed = [_parse_address(part) for part in value.split(",")]
    return [address for address in parsed if address is not None]


def _parse_internal_date(value: Any) -> datetime | None:
    """Parse the internal date.

    Args:
        value (Any): Value to process.

    Returns:
        datetime | None: The internal date.
    """
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return None
