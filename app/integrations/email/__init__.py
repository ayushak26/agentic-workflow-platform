"""Email integration — one capability, provider adapters underneath.

`build_email_service` is what the application wires into the service container.
Connections come from configuration, never from workflow YAML: a workflow says
"use connection `support_inbox`", and which mailbox and which token that means is
a deployment decision.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.observability.logging import get_logger

from .base import (
    EmailAdapter,
    EmailAdapterError,
    EmailAddress,
    EmailAmbiguousFailure,
    EmailConnection,
    EmailDraft,
    EmailMessage,
    EmailOperation,
    EmailResult,
    EmailSearchCriteria,
)
from .gmail import GmailAdapter
from .memory import InMemoryEmailAdapter
from .msgraph import MicrosoftGraphAdapter
from .service import (
    EmailConnectionError,
    EmailOperationInFlight,
    EmailOperationLedger,
    EmailService,
    idempotency_key,
)

log = get_logger(__name__)

__all__ = [
    "EmailAdapter",
    "EmailAdapterError",
    "EmailAddress",
    "EmailAmbiguousFailure",
    "EmailConnection",
    "EmailConnectionError",
    "EmailDraft",
    "EmailMessage",
    "EmailOperation",
    "EmailOperationInFlight",
    "EmailOperationLedger",
    "EmailResult",
    "EmailSearchCriteria",
    "EmailService",
    "GmailAdapter",
    "InMemoryEmailAdapter",
    "MicrosoftGraphAdapter",
    "build_email_service",
    "idempotency_key",
    "load_connections",
]

#: Environment variable holding a JSON array of connection definitions, e.g.
#:   EMAIL_CONNECTIONS='[{"id":"support_inbox","provider":"microsoft",
#:     "address":"support@example.com","allow_send":false,
#:     "credentials":{"access_token_env":"SUPPORT_GRAPH_TOKEN"}}]'
#: Tokens are referenced by env-var *name*, so the connection definition itself
#: can live in non-secret configuration.
CONNECTIONS_ENV = "EMAIL_CONNECTIONS"


def load_connections(raw: str | None = None) -> dict[str, EmailConnection]:
    """Parse configured connections and resolve their credentials.

    A malformed or unresolvable connection is skipped with a log line rather
    than raising: one broken mailbox definition must not stop the API from
    starting, and preflight reports the missing connection against the workflow
    that actually needs it.
    """
    source = raw if raw is not None else os.environ.get(CONNECTIONS_ENV, "")
    if not source.strip():
        return {}

    try:
        entries = json.loads(source)
    except json.JSONDecodeError as error:
        log.error("email.connections_unparseable", error=str(error))
        return {}
    if not isinstance(entries, list):
        log.error("email.connections_not_a_list")
        return {}

    connections: dict[str, EmailConnection] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            resolved = dict(entry)
            resolved["credentials"] = _resolve_credentials(
                entry.get("credentials") or {}
            )
            connection = EmailConnection(**resolved)
        except Exception as error:
            log.error(
                "email.connection_invalid",
                connection=entry.get("id"),
                error=str(error),
            )
            continue
        connections[connection.id] = connection
    return connections


def _resolve_credentials(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn `*_env` indirections into their values.

    `{"access_token_env": "SUPPORT_GRAPH_TOKEN"}` becomes
    `{"access_token": "<value>"}`, so the secret never appears in the
    connection definition and cannot be committed by accident.
    """
    resolved: dict[str, Any] = {}
    for key, value in raw.items():
        if key.endswith("_env") and isinstance(value, str):
            env_value = os.environ.get(value, "")
            if env_value:
                resolved[key[: -len("_env")]] = env_value
            continue
        resolved[key] = value
    return resolved


def build_email_service(
    *,
    db: Any = None,
    connections: dict[str, EmailConnection] | None = None,
    include_memory_adapter: bool = True,
) -> EmailService:
    """Assemble the service with every adapter registered.

    The in-memory adapter is included by default so a `provider: memory`
    connection can drive a Builder demo or a test through exactly the same
    service path — permission check and idempotency ledger included — as a real
    mailbox.
    """
    adapters: dict[str, EmailAdapter] = {
        "gmail": GmailAdapter(),
        "microsoft": MicrosoftGraphAdapter(),
    }
    if include_memory_adapter:
        adapters["memory"] = InMemoryEmailAdapter()

    return EmailService(
        adapters=adapters,
        connections=connections if connections is not None else load_connections(),
        ledger=EmailOperationLedger(db),
        db=db,
    )
