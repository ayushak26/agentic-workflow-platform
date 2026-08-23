"""OneDrive adapter — Microsoft Graph over the shared httpx client.

Graph differs from Drive in every detail that matters: `/children` and
`/search(q='...')` endpoints instead of a `q` filter grammar, a `folder`
facet instead of a mimeType convention, and an opaque `@odata.nextLink` full
URL for pagination instead of a bare token. All of that is confined here; the
Integration node above only ever picked "OneDrive" and an operation.
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

_API = "https://graph.microsoft.com/v1.0"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_ITEM_FIELDS = "id,name,file,folder,size,lastModifiedDateTime,webUrl,parentReference"


class OneDriveProvider(IntegrationProvider):
    """Provides the OneDriveProvider behaviour."""
    provider = "onedrive"

    def __init__(self, client: httpx.AsyncClient | None = None):
        """Initialize the OneDriveProvider.

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
                f"onedrive connection {connection.id!r} has no access_token. "
                "Connections are resolved from the secret store, not from "
                "workflow YAML."
            )
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        connection: IntegrationConnection,
        method: str,
        url: str,
        *,
        follow_redirects: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Internal helper for the request step.

        Args:
            connection (IntegrationConnection): The connection.
            method (str): The method.
            url (str): Target URL.
            follow_redirects (bool): The follow redirects (optional, default False).
            **kwargs (Any): Keyword arguments.

        Returns:
            httpx.Response: The result.
        """
        client = self._client or httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=follow_redirects
        )
        owns_client = self._client is None
        try:
            response = await client.request(
                method, url, headers=self._headers(connection), **kwargs
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise IntegrationAdapterError(
                f"onedrive {method} {url} failed: {error}"
            ) from error
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 401:
            raise IntegrationAuthError(
                f"onedrive connection {connection.id!r}'s access token was "
                "rejected — reconnect this account."
            )
        if response.status_code == 404:
            raise IntegrationNotFoundError(
                f"onedrive {method} {url} returned 404: not found or not "
                "accessible with this connection."
            )
        if response.status_code >= 400:
            raise IntegrationAdapterError(
                f"onedrive {method} {url} returned {response.status_code}: "
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
        if page_token and page_token.startswith("http"):
            response = await self._request(connection, "GET", page_token)
            return _to_page(response.json())

        path = f"/me/drive/items/{folder_id}/children" if folder_id else "/me/drive/root/children"
        response = await self._request(
            connection,
            "GET",
            f"{_API}{path}",
            params={"$select": _ITEM_FIELDS, "$top": page_size},
        )
        return _to_page(response.json())

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
        if page_token and page_token.startswith("http"):
            response = await self._request(connection, "GET", page_token)
            return _to_page(response.json())

        escaped = query.replace("'", "''")
        scope = f"/me/drive/items/{folder_id}" if folder_id else "/me/drive/root"
        response = await self._request(
            connection,
            "GET",
            f"{_API}{scope}/search(q='{escaped}')",
            params={"$select": _ITEM_FIELDS, "$top": page_size},
        )
        return _to_page(response.json())

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
            f"{_API}/me/drive/items/{file_id}",
            params={"$select": _ITEM_FIELDS},
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
        # Graph's /content redirects to a pre-authenticated blob URL — the
        # generic _request must follow it here (unlike every other call,
        # which wants the Graph JSON envelope, not a redirect target).
        response = await self._request(
            connection,
            "GET",
            f"{_API}/me/drive/items/{file_id}/content",
            follow_redirects=True,
        )
        content_type = response.headers.get("content-type", meta.mime_type or "application/octet-stream")
        return DownloadedFile(meta=meta, content=response.content, content_type=content_type)


def _to_meta(raw: dict[str, Any]) -> CloudFileMeta:
    """Internal helper for the to meta step.

    Args:
        raw (dict[str, Any]): Raw value.

    Returns:
        CloudFileMeta: The meta.
    """
    is_folder = "folder" in raw
    parent = raw.get("parentReference") or {}
    file_facet = raw.get("file") or {}
    return CloudFileMeta(
        id=str(raw.get("id") or ""),
        name=raw.get("name") or "",
        mime_type="" if is_folder else (file_facet.get("mimeType") or ""),
        is_folder=is_folder,
        size_bytes=int(raw["size"]) if raw.get("size") is not None else None,
        modified_at=_parse_timestamp(raw.get("lastModifiedDateTime")),
        web_url=raw.get("webUrl"),
        parent_id=parent.get("id"),
        provider_fields={"hash": (file_facet.get("hashes") or {}).get("quickXorHash")},
    )


def _to_page(raw: dict[str, Any]) -> Page:
    """Internal helper for the to page step.

    Args:
        raw (dict[str, Any]): Raw value.

    Returns:
        Page: The page.
    """
    items = [_to_meta(item) for item in raw.get("value", [])]
    return Page(items=items, next_page_token=raw.get("@odata.nextLink"))


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
