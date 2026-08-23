"""Durable, non-secret record of an OAuth-established Integration connection.

Split deliberately from token_vault.py: this collection
(`integration_oauth_connections`) holds exactly what the Builder's connection
picker shows — id, provider, display name, address, needs_reauth — never a
token. The token itself lives only in the encrypted token_vault, keyed by the
same connection id.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.integrations.files.base import IntegrationConnection
from app.integrations.files.token_vault import TokenVault
from app.observability.logging import get_logger

log = get_logger(__name__)

CONNECTIONS_COLLECTION = "integration_oauth_connections"


async def save_connection_record(
    db: Any,
    *,
    connection_id: str,
    provider: str,
    display_name: str,
    address: str,
) -> None:
    """Save the connection record.

    Args:
        db (Any): Mongo database handle.
        connection_id (str): The connection id.
        provider (str): Provider name.
        display_name (str): The display name.
        address (str): The address.
    """
    await db[CONNECTIONS_COLLECTION].update_one(
        {"_id": connection_id},
        {
            "$set": {
                "provider": provider,
                "display_name": display_name,
                "address": address,
                # A reconnect always clears a stale reauth flag — a fresh
                # consent is exactly what resolves it.
                "needs_reauth": False,
                "updated_at": datetime.now(UTC),
            },
            "$setOnInsert": {"created_at": datetime.now(UTC)},
        },
        upsert=True,
    )


async def delete_connection_record(db: Any, connection_id: str) -> None:
    """Delete the connection record.

    Args:
        db (Any): Mongo database handle.
        connection_id (str): The connection id.
    """
    await db[CONNECTIONS_COLLECTION].delete_one({"_id": connection_id})
    await TokenVault(db).forget(connection_id)


async def set_needs_reauth(db: Any, connection_id: str, needs_reauth: bool) -> None:
    """Persist the reauth flag so the Builder can show it without a live
    provider call — set on a failed refresh, cleared on any success."""
    await db[CONNECTIONS_COLLECTION].update_one(
        {"_id": connection_id}, {"$set": {"needs_reauth": needs_reauth}}
    )


async def load_dynamic_connections(db: Any) -> dict[str, IntegrationConnection]:
    """Every OAuth-established connection, with its tokens attached.

    A record whose tokens have gone missing from the vault (e.g. `forget`
    ran but the connection record write failed) is skipped with a log line
    — the same "one broken definition must not stop the others" tolerance
    app/integrations/email/connections_store.py already applies.
    """
    vault = TokenVault(db)
    connections: dict[str, IntegrationConnection] = {}
    cursor = db[CONNECTIONS_COLLECTION].find({})
    async for record in cursor:
        connection_id = record["_id"]
        try:
            tokens = await vault.load(connection_id)
        except Exception as error:
            log.error(
                "integration.oauth_connection_tokens_unavailable",
                connection_id=connection_id,
                error=str(error),
            )
            continue
        if tokens is None:
            log.warning(
                "integration.oauth_connection_missing_tokens",
                connection_id=connection_id,
            )
            continue
        connections[connection_id] = IntegrationConnection(
            id=connection_id,
            provider=record["provider"],
            display_name=record.get("display_name") or connection_id,
            address=record.get("address", ""),
            needs_reauth=bool(record.get("needs_reauth", False)),
            credentials={
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token"),
                "expires_at": tokens["expires_at"].isoformat(),
            },
        )
    return connections
