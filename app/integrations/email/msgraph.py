"""Microsoft Graph adapter — Outlook / Microsoft 365 mail.

Graph differs from Gmail in every detail that matters: OData `$filter`/`$search`
instead of a query string, JSON message objects instead of base64 MIME, a
dedicated `/reply` endpoint that quotes and threads server-side, and
`conversationId` instead of `threadId`. All of that is confined here; the node
above only ever picked "Microsoft Outlook" and an operation.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.observability.logging import get_logger

from .base import (
    EmailAdapter,
    EmailAdapterError,
    EmailAmbiguousFailure,
    EmailAddress,
    EmailAttachmentRef,
    EmailConnection,
    EmailDraft,
    EmailMessage,
    EmailSearchCriteria,
)

log = get_logger(__name__)

_API = "https://graph.microsoft.com/v1.0"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_MESSAGE_FIELDS = (
    "id,conversationId,subject,bodyPreview,body,from,toRecipients,"
    "ccRecipients,receivedDateTime,isRead,hasAttachments,internetMessageId"
)


class MicrosoftGraphAdapter(EmailAdapter):
    """Provides the MicrosoftGraphAdapter behaviour."""
    provider = "microsoft"

    def __init__(self, client: httpx.AsyncClient | None = None):
        """Initialize the MicrosoftGraphAdapter.

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
                f"microsoft connection {connection.id!r} has no access_token. "
                "Connections are resolved from the secret store, not from "
                "workflow YAML."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _mailbox(self, connection: EmailConnection) -> str:
        """Graph addresses either the signed-in user or a specific mailbox.

        A delegated user token uses /me; an application token must name the
        mailbox, which is why the connection carries `address`.
        """
        if connection.credentials.get("mode") == "application" and connection.address:
            return f"/users/{connection.address}"
        return "/me"

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
            if side_effect:
                raise EmailAmbiguousFailure(
                    f"graph {method} {path} did not return a definitive "
                    f"result: {error}"
                ) from error
            raise EmailAdapterError(f"graph {method} {path} failed: {error}") from error
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 500 and side_effect:
            raise EmailAmbiguousFailure(
                f"graph {method} {path} returned {response.status_code}; the "
                "outcome is unknown"
            )
        if response.status_code >= 400:
            raise EmailAdapterError(
                f"graph {method} {path} returned {response.status_code}: "
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
        params: dict[str, Any] = {
            "$top": criteria.max_results,
            "$select": _MESSAGE_FIELDS,
            "$orderby": "receivedDateTime desc",
        }
        filters = _graph_filters(criteria)
        if filters:
            params["$filter"] = " and ".join(filters)
        if criteria.query:
            # Graph rejects $search combined with $orderby, so full-text search
            # gives up server-side ordering rather than the search itself.
            params["$search"] = f'"{criteria.query}"'
            params.pop("$orderby", None)

        folder = criteria.folder or "inbox"
        listing = await self._request(
            connection,
            "GET",
            f"{self._mailbox(connection)}/mailFolders/{folder}/messages",
            params=params,
        )
        return [_to_message(item) for item in listing.get("value", [])]

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
            f"{self._mailbox(connection)}/messages/{message_id}",
            params={"$select": _MESSAGE_FIELDS},
        )
        message = _to_message(raw)
        if raw.get("hasAttachments"):
            attachments = await self._request(
                connection,
                "GET",
                f"{self._mailbox(connection)}/messages/{message_id}/attachments",
                params={"$select": "id,name,contentType,size"},
            )
            message.attachments = [
                EmailAttachmentRef(
                    id=str(item.get("id") or ""),
                    name=item.get("name") or "attachment",
                    content_type=item.get("contentType")
                    or "application/octet-stream",
                    size_bytes=int(item.get("size") or 0),
                )
                for item in attachments.get("value", [])
            ]
        return message

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
        created = await self._request(
            connection,
            "POST",
            f"{self._mailbox(connection)}/messages",
            side_effect=True,
            json=_draft_payload(draft),
        )
        return str(created.get("id") or "")

    async def send(self, connection: EmailConnection, draft: EmailDraft) -> str:
        # sendMail returns 202 with an empty body — no message id. Creating the
        # draft first and sending it means the caller gets a real id back, which
        # is what makes an operation auditable after the fact.
        """Send the result.

        Args:
            connection (EmailConnection): The connection.
            draft (EmailDraft): The draft.

        Returns:
            str: The result.
        """
        message_id = await self.create_draft(connection, draft)
        await self._request(
            connection,
            "POST",
            f"{self._mailbox(connection)}/messages/{message_id}/send",
            side_effect=True,
        )
        return message_id

    async def reply(self, connection: EmailConnection, draft: EmailDraft) -> str:
        """Use Graph's own reply endpoint so it quotes and threads correctly."""
        target = draft.in_reply_to_message_id
        if not target:
            return await self.send(connection, draft)
        await self._request(
            connection,
            "POST",
            f"{self._mailbox(connection)}/messages/{target}/reply",
            side_effect=True,
            json={
                "message": {
                    "toRecipients": [
                        _recipient(address) for address in draft.to
                    ],
                    "ccRecipients": [
                        _recipient(address) for address in draft.cc
                    ],
                },
                "comment": draft.body_text,
            },
        )
        # /reply returns 202 with no body; the reply is threaded under the
        # original, so the original id is the honest identifier to record.
        return target


def _graph_filters(criteria: EmailSearchCriteria) -> list[str]:
    """Internal helper for the graph filters step.

    Args:
        criteria (EmailSearchCriteria): The criteria.

    Returns:
        list[str]: The filters.
    """
    filters: list[str] = []
    if criteria.unread_only:
        filters.append("isRead eq false")
    if criteria.has_attachments:
        filters.append("hasAttachments eq true")
    if criteria.from_address:
        escaped = criteria.from_address.replace("'", "''")
        filters.append(f"from/emailAddress/address eq '{escaped}'")
    if criteria.subject_contains:
        escaped = criteria.subject_contains.replace("'", "''")
        filters.append(f"contains(subject, '{escaped}')")
    if criteria.newer_than_days:
        cutoff = datetime.now(UTC).timestamp() - criteria.newer_than_days * 86400
        stamp = datetime.fromtimestamp(cutoff, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        filters.append(f"receivedDateTime ge {stamp}")
    return filters


def _recipient(address: EmailAddress) -> dict[str, Any]:
    """Internal helper for the recipient step.

    Args:
        address (EmailAddress): The address.

    Returns:
        dict[str, Any]: The result.
    """
    entry: dict[str, Any] = {"emailAddress": {"address": address.email}}
    if address.name:
        entry["emailAddress"]["name"] = address.name
    return entry


def _draft_payload(draft: EmailDraft) -> dict[str, Any]:
    """Draft the payload.

    Args:
        draft (EmailDraft): The draft.

    Returns:
        dict[str, Any]: The payload.
    """
    payload: dict[str, Any] = {
        "subject": draft.subject,
        "body": {
            "contentType": "HTML" if draft.body_html else "Text",
            "content": draft.body_html or draft.body_text,
        },
        "toRecipients": [_recipient(address) for address in draft.to],
    }
    if draft.cc:
        payload["ccRecipients"] = [_recipient(address) for address in draft.cc]
    if draft.bcc:
        payload["bccRecipients"] = [_recipient(address) for address in draft.bcc]
    if draft.in_reply_to_message_id:
        payload["internetMessageHeaders"] = [
            {"name": "In-Reply-To", "value": draft.in_reply_to_message_id}
        ]
    return payload


def _address(raw: dict[str, Any] | None) -> EmailAddress | None:
    """Internal helper for the address step.

    Args:
        raw (dict[str, Any] | None): Raw value.

    Returns:
        EmailAddress | None: The result.
    """
    if not raw:
        return None
    inner = raw.get("emailAddress") or {}
    email = inner.get("address")
    if not email:
        return None
    return EmailAddress(email=email, name=inner.get("name") or None)


def _addresses(raw: list[dict[str, Any]] | None) -> list[EmailAddress]:
    """Internal helper for the addresses step.

    Args:
        raw (list[dict[str, Any]] | None): Raw value.

    Returns:
        list[EmailAddress]: The result.
    """
    parsed = [_address(item) for item in raw or []]
    return [item for item in parsed if item is not None]


def _to_message(raw: dict[str, Any]) -> EmailMessage:
    """Internal helper for the to message step.

    Args:
        raw (dict[str, Any]): Raw value.

    Returns:
        EmailMessage: The message.
    """
    body = raw.get("body") or {}
    is_html = str(body.get("contentType", "")).lower() == "html"
    content = body.get("content") or ""
    return EmailMessage(
        id=str(raw.get("id") or ""),
        thread_id=raw.get("conversationId"),
        subject=raw.get("subject") or "",
        body_text=(raw.get("bodyPreview") or "") if is_html else content,
        body_html=content if is_html else None,
        from_address=_address(raw.get("from")),
        to=_addresses(raw.get("toRecipients")),
        cc=_addresses(raw.get("ccRecipients")),
        received_at=_parse_timestamp(raw.get("receivedDateTime")),
        is_unread=not bool(raw.get("isRead", True)),
        labels=[],
        provider_fields={
            "internet_message_id": raw.get("internetMessageId"),
            "has_attachments": raw.get("hasAttachments"),
        },
    )


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse the timestamp.

    Args:
        value (Any): Value to process.

    Returns:
        datetime | None: The timestamp.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
