"""Google Drive adapter — Drive API v3 over the shared httpx client.

Everything Drive-specific is confined here: the `q` query grammar, folder
detection by mimeType, and native Google Docs/Sheets/Slides needing an
`/export` call instead of `alt=media` (which 403s on them). The Integration
node above sees none of it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

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

log = get_logger(__name__)

_API = "https://www.googleapis.com/drive/v3"
_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_FOLDER_MIME = "application/vnd.google-apps.folder"
_FIELDS = "nextPageToken,files(id,name,mimeType,size,modifiedTime,webViewLink,parents,md5Checksum)"

#: Native Google types have no downloadable bytes — they must be exported to
#: a concrete format instead of fetched with `alt=media`.
_EXPORT_MIME_MAP: dict[str, str] = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
}


class GoogleDriveProvider(IntegrationProvider):
    """Provides the GoogleDriveProvider behaviour."""
    provider = "google_drive"

    def __init__(self, client: httpx.AsyncClient | None = None):
        # An injected client is how tests drive this adapter against a
        # transport mock, and how a deployment shares one connection pool.
        """Initialize the GoogleDriveProvider.

        Args:
            client (httpx.AsyncClient | None): Client instance (optional, default None).
        """
        self._client = client

    def _headers(self, connection: IntegrationConnection) -> dict[str, str]:
        """Internal helper for the headers step.

        Args:
            connection (IntegrationConnection): The connection.

        Returns:
            dict[str, str]: The result.
        """
        token = connection.credentials.get("access_token")
        if not token:
            raise IntegrationAdapterError(
                f"google_drive connection {connection.id!r} has no access_token. "
                "Connections are resolved from the secret store, not from "
                "workflow YAML."
            )
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        connection: IntegrationConnection,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Internal helper for the request step.

        Args:
            connection (IntegrationConnection): The connection.
            method (str): The method.
            url (str): Target URL.
            **kwargs (Any): Keyword arguments.

        Returns:
            httpx.Response: The result.
        """
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT)
        owns_client = self._client is None
        try:
            response = await client.request(
                method, url, headers=self._headers(connection), **kwargs
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise IntegrationAdapterError(
                f"google_drive {method} {url} failed: {error}"
            ) from error
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 401:
            raise IntegrationAuthError(
                f"google_drive connection {connection.id!r}'s access token "
                "was rejected — reconnect this account."
            )
        if response.status_code == 404:
            raise IntegrationNotFoundError(
                f"google_drive {method} {url} returned 404: not found or "
                "not accessible with this connection."
            )
        if response.status_code >= 400:
            raise IntegrationAdapterError(
                f"google_drive {method} {url} returned {response.status_code}: "
                f"{response.text[:300]}"
            )
        return response

    async def list_folder(
        self,
        connection: IntegrationConnection,
        *,
        folder_id: str | None,
        page_size: int,
        page_token: str | None,
    ) -> Page:
        """List the folder.

        Args:
            connection (IntegrationConnection): The connection.
            folder_id (str | None): The folder id.
            page_size (int): The page size.
            page_token (str | None): The page token.

        Returns:
            Page: The folder.
        """
        parent = folder_id or "root"
        response = await self._request(
            connection,
            "GET",
            f"{_API}/files",
            params={
                "q": f"'{parent}' in parents and trashed=false",
                "fields": _FIELDS,
                "pageSize": page_size,
                "pageToken": page_token,
                "spaces": "drive",
            },
        )
        return _to_page(response.json(), parent_id=folder_id)

    async def search_files(
        self,
        connection: IntegrationConnection,
        *,
        query: str,
        folder_id: str | None,
        page_size: int,
        page_token: str | None,
    ) -> Page:
        """Search the files.

        Args:
            connection (IntegrationConnection): The connection.
            query (str): Query filter.
            folder_id (str | None): The folder id.
            page_size (int): The page size.
            page_token (str | None): The page token.

        Returns:
            Page: The files.
        """
        escaped = query.replace("'", "\\'")
        clauses = [f"name contains '{escaped}'", "trashed=false"]
        if folder_id:
            clauses.append(f"'{folder_id}' in parents")
        response = await self._request(
            connection,
            "GET",
            f"{_API}/files",
            params={
                "q": " and ".join(clauses),
                "fields": _FIELDS,
                "pageSize": page_size,
                "pageToken": page_token,
                "spaces": "drive",
            },
        )
        return _to_page(response.json(), parent_id=folder_id)

    async def get_file_meta(
        self, connection: IntegrationConnection, *, file_id: str
    ) -> CloudFileMeta:
        """Return the file meta.

        Args:
            connection (IntegrationConnection): The connection.
            file_id (str): The file id.

        Returns:
            CloudFileMeta: The file meta.
        """
        response = await self._request(
            connection,
            "GET",
            f"{_API}/files/{file_id}",
            params={
                "fields": "id,name,mimeType,size,modifiedTime,webViewLink,parents,md5Checksum"
            },
        )
        return _to_meta(response.json())

    async def download_file(
        self, connection: IntegrationConnection, *, file_id: str
    ) -> DownloadedFile:
        """Download the file.

        Args:
            connection (IntegrationConnection): The connection.
            file_id (str): The file id.

        Returns:
            DownloadedFile: The file.
        """
        meta = await self.get_file_meta(connection, file_id=file_id)
        export_mime = _EXPORT_MIME_MAP.get(meta.mime_type)
        if export_mime:
            response = await self._request(
                connection,
                "GET",
                f"{_API}/files/{file_id}/export",
                params={"mimeType": export_mime},
            )
            return DownloadedFile(meta=meta, content=response.content, content_type=export_mime)

        response = await self._request(
            connection,
            "GET",
            f"{_API}/files/{file_id}",
            params={"alt": "media"},
        )
        return DownloadedFile(
            meta=meta,
            content=response.content,
            content_type=meta.mime_type or "application/octet-stream",
        )


def _to_meta(raw: dict[str, Any], *, parent_id: str | None = None) -> CloudFileMeta:
    """Internal helper for the to meta step.

    Args:
        raw (dict[str, Any]): Raw value.
        parent_id (str | None): The parent id (optional, default None).

    Returns:
        CloudFileMeta: The meta.
    """
    mime = raw.get("mimeType", "")
    parents = raw.get("parents") or []
    return CloudFileMeta(
        id=str(raw.get("id") or ""),
        name=raw.get("name") or "",
        mime_type=mime,
        is_folder=mime == _FOLDER_MIME,
        size_bytes=int(raw["size"]) if raw.get("size") is not None else None,
        modified_at=_parse_timestamp(raw.get("modifiedTime")),
        web_url=raw.get("webViewLink"),
        parent_id=parent_id or (parents[0] if parents else None),
        provider_fields={"md5_checksum": raw.get("md5Checksum")},
    )


def _to_page(raw: dict[str, Any], *, parent_id: str | None) -> Page:
    """Internal helper for the to page step.

    Args:
        raw (dict[str, Any]): Raw value.
        parent_id (str | None): The parent id.

    Returns:
        Page: The page.
    """
    items = [_to_meta(item, parent_id=parent_id) for item in raw.get("files", [])]
    return Page(items=items, next_page_token=raw.get("nextPageToken"))


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
