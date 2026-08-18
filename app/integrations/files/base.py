"""Provider-neutral cloud-file contract.

The workflow author sees one Integration capability with a provider
connection and an operation. Everything that differs between Google Drive and
OneDrive — auth, pagination, folder/search query syntax, native-document
export — lives in an adapter under this contract:

    Integration node  (provider + operation selector, references a connection)
              │
       IntegrationService  (connection resolution, refresh, the operation switch)
              │
      ┌───────┴────────┐
  Google Drive      OneDrive        (a future Dropbox/Box/SharePoint adapter
   adapter           adapter         slots in the same way)

The node never sees an OAuth token: it references a connection id, and the
service resolves credentials. Every operation here is a read — there is no
idempotency ledger, unlike app/integrations/email/, because nothing this
capability does can be sent twice by accident.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IntegrationProviderName = Literal["google_drive", "onedrive"]


class CloudFileMeta(BaseModel):
    """Metadata only — never bytes. What a folder listing or search returns."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    mime_type: str = ""
    is_folder: bool = False
    size_bytes: int | None = None
    modified_at: datetime | None = None
    web_url: str | None = None
    parent_id: str | None = None
    #: Provider-specific extras an author may need but the contract shouldn't
    #: normalise (Drive's md5Checksum, Graph's cTag).
    provider_fields: dict[str, Any] = Field(default_factory=dict)


class DownloadedFile(BaseModel):
    """Bytes, held only long enough to hand off to object storage.

    Never enters workflow state or a retry checkpoint directly — the node's
    run() writes `content` into MinIO and returns a CloudFileRef pointer, the
    same discipline app/runtime/schema.py's WorkflowFileRef follows.
    """

    model_config = ConfigDict(extra="forbid")

    meta: CloudFileMeta
    content: bytes = Field(repr=False)
    content_type: str = "application/octet-stream"


class Page(BaseModel):
    """One page of a folder listing or search result."""

    model_config = ConfigDict(extra="forbid")

    items: list[CloudFileMeta] = Field(default_factory=list)
    next_page_token: str | None = None


class IntegrationConnection(BaseModel):
    """A named, credential-bearing connection. Workflow YAML references only
    `id`; the secret material is resolved by the service at run time.

    Deployment-scoped, matching app/integrations/email/'s EmailConnection —
    any consultant can select any connected Drive/OneDrive account. Unlike
    EmailConnection there is no `allow_send`-style safety switch: nothing an
    IntegrationProvider does writes anything.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    provider: IntegrationProviderName
    display_name: str = ""
    #: The account this connection reads as. Recorded on every result so a
    #: workflow export shows which account a file came from.
    address: str = ""
    #: Resolved from the token vault by the connection registry — never read
    #: from workflow YAML.
    credentials: dict[str, Any] = Field(default_factory=dict, repr=False)
    #: Set when a refresh attempt failed with an auth-type error (revoked or
    #: expired refresh token) — surfaced to the Builder so a stale connection
    #: shows "Reauthentication required" without a live provider call.
    needs_reauth: bool = False


class IntegrationAdapterError(RuntimeError):
    """A provider call failed in a way the caller can describe to a user."""


class IntegrationAuthError(IntegrationAdapterError):
    """The access token was rejected — revoked or expired, reauth needed."""


class IntegrationNotFoundError(IntegrationAdapterError):
    """The referenced file or folder does not exist (or is not accessible)."""


class IntegrationProvider(ABC):
    """What every cloud-storage adapter must offer.

    Four read methods, matching the operations IntegrationService dispatches.
    No `connect`/`disconnect` — OAuth is generic and lives in oauth.py /
    app/api/integration_oauth.py, not on the provider object.
    """

    provider: str

    @abstractmethod
    async def list_folder(
        self,
        connection: IntegrationConnection,
        *,
        folder_id: str | None,
        page_size: int,
        page_token: str | None,
    ) -> Page:
        ...

    @abstractmethod
    async def search_files(
        self,
        connection: IntegrationConnection,
        *,
        query: str,
        folder_id: str | None,
        page_size: int,
        page_token: str | None,
    ) -> Page:
        ...

    @abstractmethod
    async def get_file_meta(
        self, connection: IntegrationConnection, *, file_id: str
    ) -> CloudFileMeta:
        ...

    @abstractmethod
    async def download_file(
        self, connection: IntegrationConnection, *, file_id: str
    ) -> DownloadedFile:
        ...
