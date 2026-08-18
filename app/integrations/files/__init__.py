"""Cloud file integrations — one capability, provider adapters underneath.

`build_integration_service` is what the application wires into the service
container. Connections come from OAuth (someone clicked "Connect Google
Drive"/"Connect OneDrive" in the Builder — see app/api/integration_oauth.py)
or, for parity with app/integrations/email/, an optional static/env-var
declaration: a workflow says "use connection `shared_drive`", and which
account and which token that means is a deployment decision, never workflow
content.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.observability.logging import get_logger

from .base import (
    CloudFileMeta,
    DownloadedFile,
    IntegrationAdapterError,
    IntegrationAuthError,
    IntegrationConnection,
    IntegrationNotFoundError,
    IntegrationProvider,
    Page,
)
from .google_drive import GoogleDriveProvider
from .onedrive import OneDriveProvider
from .service import (
    IntegrationConnectionError,
    IntegrationOperation,
    IntegrationResult,
    IntegrationService,
)

log = get_logger(__name__)

__all__ = [
    "CloudFileMeta",
    "DownloadedFile",
    "GoogleDriveProvider",
    "IntegrationAdapterError",
    "IntegrationAuthError",
    "IntegrationConnection",
    "IntegrationConnectionError",
    "IntegrationNotFoundError",
    "IntegrationOperation",
    "IntegrationProvider",
    "IntegrationResult",
    "IntegrationService",
    "OneDriveProvider",
    "Page",
    "build_integration_service",
    "load_connections",
]

#: Environment variable holding a JSON array of connection definitions, e.g.
#:   INTEGRATION_CONNECTIONS='[{"id":"shared_drive","provider":"google_drive",
#:     "address":"team@example.com","credentials":{"access_token_env":"SHARED_DRIVE_TOKEN"}}]'
#: Tokens are referenced by env-var *name*, so the connection definition
#: itself can live in non-secret configuration. OAuth is the primary path;
#: this exists for parity with email's static-connection option.
CONNECTIONS_ENV = "INTEGRATION_CONNECTIONS"


def load_connections(raw: str | None = None) -> dict[str, IntegrationConnection]:
    """Parse configured connections and resolve their credentials.

    A malformed or unresolvable connection is skipped with a log line rather
    than raising: one broken definition must not stop the API from starting.
    """
    source = raw if raw is not None else os.environ.get(CONNECTIONS_ENV, "")
    if not source.strip():
        return {}

    try:
        entries = json.loads(source)
    except json.JSONDecodeError as error:
        log.error("integration.connections_unparseable", error=str(error))
        return {}
    if not isinstance(entries, list):
        log.error("integration.connections_not_a_list")
        return {}

    connections: dict[str, IntegrationConnection] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            resolved = dict(entry)
            resolved["credentials"] = _resolve_credentials(entry.get("credentials") or {})
            connection = IntegrationConnection(**resolved)
        except Exception as error:
            log.error(
                "integration.connection_invalid",
                connection=entry.get("id"),
                error=str(error),
            )
            continue
        connections[connection.id] = connection
    return connections


def _resolve_credentials(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn `*_env` indirections into their values.

    `{"access_token_env": "SHARED_DRIVE_TOKEN"}` becomes
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


def build_integration_service(
    *,
    db: Any = None,
    connections: dict[str, IntegrationConnection] | None = None,
) -> IntegrationService:
    """Assemble the service with every provider adapter registered."""
    providers: dict[str, IntegrationProvider] = {
        "google_drive": GoogleDriveProvider(),
        "onedrive": OneDriveProvider(),
    }
    return IntegrationService(
        providers=providers,
        connections=connections if connections is not None else load_connections(),
        db=db,
    )
