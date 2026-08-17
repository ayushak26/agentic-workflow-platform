"""Durable, non-secret record of an OAuth-established email connection.

Split deliberately from token_vault.py: this collection (`email_oauth_connections`)
holds exactly what app/api/builder.py's GET /email/connections already shows
in the Builder — id, provider, display name, address, allow_send — never a
token. The token itself lives only in the encrypted token_vault, keyed by
the same connection id.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.integrations.email.base import EmailConnection
from app.integrations.email.token_vault import TokenVault
from app.observability.logging import get_logger

log = get_logger(__name__)

CONNECTIONS_COLLECTION = "email_oauth_connections"


async def save_connection_record(
    db: Any,
    *,
    connection_id: str,
    provider: str,
    display_name: str,
    address: str,
) -> None:
    await db[CONNECTIONS_COLLECTION].update_one(
        {"_id": connection_id},
        {
            "$set": {
                "provider": provider,
                "display_name": display_name,
                "address": address,
                "updated_at": datetime.now(UTC),
            },
            "$setOnInsert": {"allow_send": False, "created_at": datetime.now(UTC)},
        },
        upsert=True,
    )


async def delete_connection_record(db: Any, connection_id: str) -> None:
    await db[CONNECTIONS_COLLECTION].delete_one({"_id": connection_id})
    await TokenVault(db).forget(connection_id)


async def set_allow_send(db: Any, connection_id: str, allow_send: bool) -> bool:
    """Returns True if a record was actually updated (the connection exists)."""
    result = await db[CONNECTIONS_COLLECTION].update_one(
        {"_id": connection_id}, {"$set": {"allow_send": allow_send}}
    )
    return result.matched_count > 0


async def load_dynamic_connections(db: Any) -> dict[str, EmailConnection]:
    """Every OAuth-established connection, with its tokens attached.

    A record whose tokens have gone missing from the vault (e.g. `forget`
    ran but the connection record write failed) is skipped with a log line
    — the same "one broken definition must not stop the others" tolerance
    app/integrations/email/__init__.py's load_connections already applies
    to the static, env-var-configured connections.
    """
    vault = TokenVault(db)
    connections: dict[str, EmailConnection] = {}
    cursor = db[CONNECTIONS_COLLECTION].find({})
    async for record in cursor:
        connection_id = record["_id"]
        try:
            tokens = await vault.load(connection_id)
        except Exception as error:
            log.error(
                "email.oauth_connection_tokens_unavailable",
                connection_id=connection_id,
                error=str(error),
            )
            continue
        if tokens is None:
            log.warning(
                "email.oauth_connection_missing_tokens",
                connection_id=connection_id,
            )
            continue
        connections[connection_id] = EmailConnection(
            id=connection_id,
            provider=record["provider"],
            display_name=record.get("display_name") or connection_id,
            address=record.get("address", ""),
            allow_send=bool(record.get("allow_send", False)),
            credentials={
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "expires_at": tokens["expires_at"].isoformat(),
            },
        )
    return connections
