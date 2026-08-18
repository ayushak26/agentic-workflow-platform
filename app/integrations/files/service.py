"""IntegrationService — connection resolution, refresh, and the operation switch.

This is the layer the Integration node talks to. Provider-specific complexity
stays *below* the node contract: the author picks "Google Drive" or
"OneDrive" and an operation, and this service is what knows how to keep an
access token fresh and which provider method each operation maps to.

Unlike app/integrations/email/service.py there is no idempotency ledger here
— every operation this capability offers is a read, so nothing needs a
reserve-before-act discipline.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.observability.logging import get_logger

from .base import (
    CloudFileMeta,
    DownloadedFile,
    IntegrationAdapterError,
    IntegrationAuthError,
    IntegrationConnection,
    IntegrationProvider,
)

#: How much lead time to refresh an OAuth token before it actually expires —
#: generous, since a token that expires mid-request would surface as a
#: confusing 401 deep inside an adapter call instead of a clean
#: refresh-then-retry here.
_REFRESH_LEAD_TIME = timedelta(minutes=5)

log = get_logger(__name__)

IntegrationOperation = Literal[
    "select_file", "select_folder", "search_files", "get_file", "list_folder"
]


def _now() -> datetime:
    return datetime.now(UTC)


class IntegrationConnectionError(IntegrationAdapterError):
    """The referenced connection does not exist, or has no adapter registered."""


def _as_id_list(value: str | list[str] | None) -> list[str]:
    """Normalize a config field that may hold one id, several, or none.

    A single templated string is the common case (`{{outputs.x.first.id}}`);
    a list is what a multi-select in the file browser, or a mapped list of
    ids from an upstream step, produces. Either way the operation switch
    below only ever deals with "a list of ids", 1-or-more."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if item]


class IntegrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    provider: str
    connection_id: str
    #: list_folder / search_files — everything a listing/search turned up.
    files: list[CloudFileMeta] = Field(default_factory=list)
    #: select_file / select_folder / get_file — metadata for whichever id(s)
    #: were acted on, one entry per id regardless of how many were given.
    selected_files: list[CloudFileMeta] = Field(default_factory=list)
    #: Convenience: selected_files[0], for the common single-id case.
    file: CloudFileMeta | None = None
    #: Only set for get_file — the service layer's own boundary for "bytes
    #: never cross further than they have to". The node's run() is what
    #: writes these into object storage and drops them from workflow state.
    downloaded_list: list[DownloadedFile] = Field(default_factory=list)
    #: Convenience: downloaded_list[0], for the common single-file case.
    downloaded: DownloadedFile | None = None
    next_page_token: str | None = None


class IntegrationService:
    """One capability, many providers, one place that owns token refresh."""

    def __init__(
        self,
        providers: dict[str, IntegrationProvider],
        connections: dict[str, IntegrationConnection],
        db: Any = None,
    ):
        self.providers = providers
        self.connections = connections
        # Only used for OAuth-issued connections' token refresh (see
        # _refreshed below) — a deployment with no connections yet never
        # needs it and `db=None` is fine.
        self._db = db

    # -- connection resolution -----------------------------------------

    def connection(self, connection_id: str) -> IntegrationConnection:
        found = self.connections.get(connection_id)
        if found is None:
            raise IntegrationConnectionError(
                f"integration connection {connection_id!r} is not configured. "
                f"Available: {sorted(self.connections) or 'none'}"
            )
        return found

    def add_connection(self, connection: IntegrationConnection) -> None:
        """Adds or replaces a connection at runtime — how a newly completed
        OAuth connect (app/api/integration_oauth.py) becomes usable
        immediately, with no restart needed."""
        self.connections[connection.id] = connection

    def remove_connection(self, connection_id: str) -> None:
        self.connections.pop(connection_id, None)

    async def _refreshed(self, connection: IntegrationConnection) -> IntegrationConnection:
        """Refresh this connection's access token if it's near/past expiry.

        Centralized here (not in the adapters) so google_drive.py/onedrive.py
        never need to know tokens can expire at all; they just read
        connection.credentials. Mirrors EmailService._refreshed exactly,
        with one addition: a refresh failure persists needs_reauth so the
        Builder can show "Reauthentication required" without a live call.
        """
        expires_at_raw = connection.credentials.get("expires_at")
        refresh_token = connection.credentials.get("refresh_token")
        if not expires_at_raw or not refresh_token:
            return connection
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            return connection
        if _now() < expires_at - _REFRESH_LEAD_TIME:
            return connection

        from . import oauth  # local import: only OAuth-issued connections need this dependency

        try:
            tokens = await oauth.refresh_access_token(
                connection.provider, refresh_token=refresh_token
            )
        except Exception as error:
            log.error(
                "integration.token_refresh_failed",
                connection_id=connection.id,
                error=str(error),
            )
            if self._db is not None:
                from .connections_store import set_needs_reauth

                await set_needs_reauth(self._db, connection.id, True)
            reauth_marked = connection.model_copy(update={"needs_reauth": True})
            self.connections[connection.id] = reauth_marked
            raise IntegrationAuthError(
                f"connection {connection.id!r}'s access token expired and "
                f"could not be refreshed: {error}"
            ) from error

        new_expires_at = _now() + timedelta(seconds=tokens.expires_in_seconds)
        if self._db is not None:
            from .token_vault import TokenVault

            await TokenVault(self._db).store(
                connection.id,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_in_seconds=tokens.expires_in_seconds,
            )
            from .connections_store import set_needs_reauth

            await set_needs_reauth(self._db, connection.id, False)
        refreshed = connection.model_copy(update={
            "needs_reauth": False,
            "credentials": {
                **connection.credentials,
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token or refresh_token,
                "expires_at": new_expires_at.isoformat(),
            },
        })
        self.connections[connection.id] = refreshed
        log.info("integration.token_refreshed", connection_id=connection.id)
        return refreshed

    def provider_for(self, connection: IntegrationConnection) -> IntegrationProvider:
        found = self.providers.get(connection.provider)
        if found is None:
            raise IntegrationConnectionError(
                f"no adapter is registered for provider {connection.provider!r}"
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
                "needs_reauth": connection.needs_reauth,
            }
            for connection in self.connections.values()
        ]

    # -- the operation switch ------------------------------------------

    async def execute(
        self,
        *,
        connection_id: str,
        operation: IntegrationOperation,
        folder_id: str | list[str] | None = None,
        file_id: str | list[str] | None = None,
        query: str = "",
        page_size: int = 25,
        page_token: str | None = None,
    ) -> IntegrationResult:
        connection = await self._refreshed(self.connection(connection_id))
        provider = self.provider_for(connection)

        if operation == "list_folder":
            # Listing scopes exactly one folder — a list here would mean
            # "list several folders", which is what repeated list_folder
            # steps (or select_folder + a fan-out) are for, not this call.
            scope = _as_id_list(folder_id)
            page = await provider.list_folder(
                connection, folder_id=scope[0] if scope else None,
                page_size=page_size, page_token=page_token,
            )
            return IntegrationResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                files=page.items,
                next_page_token=page.next_page_token,
            )

        if operation == "search_files":
            if not query:
                raise IntegrationAdapterError("search_files needs a query")
            scope = _as_id_list(folder_id)
            page = await provider.search_files(
                connection,
                query=query,
                folder_id=scope[0] if scope else None,
                page_size=page_size,
                page_token=page_token,
            )
            return IntegrationResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                files=page.items,
                next_page_token=page.next_page_token,
            )

        if operation == "select_folder":
            ids = _as_id_list(folder_id)
            if not ids:
                raise IntegrationAdapterError("select_folder needs a folder_id")
            metas = list(await asyncio.gather(
                *(provider.get_file_meta(connection, file_id=i) for i in ids)
            ))
            return IntegrationResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                selected_files=metas,
                file=metas[0] if metas else None,
            )

        if operation == "select_file":
            ids = _as_id_list(file_id)
            if not ids:
                raise IntegrationAdapterError("select_file needs a file_id")
            metas = list(await asyncio.gather(
                *(provider.get_file_meta(connection, file_id=i) for i in ids)
            ))
            return IntegrationResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                selected_files=metas,
                file=metas[0] if metas else None,
            )

        if operation == "get_file":
            ids = _as_id_list(file_id)
            if not ids:
                raise IntegrationAdapterError("get_file needs a file_id — map it from a list_folder/search_files step")
            downloads = list(await asyncio.gather(
                *(provider.download_file(connection, file_id=i) for i in ids)
            ))
            return IntegrationResult(
                operation=operation,
                provider=connection.provider,
                connection_id=connection.id,
                selected_files=[d.meta for d in downloads],
                file=downloads[0].meta if downloads else None,
                downloaded_list=downloads,
                downloaded=downloads[0] if downloads else None,
            )

        raise IntegrationAdapterError(f"unknown operation {operation!r}")
