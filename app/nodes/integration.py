"""IntegrationAgent — one cloud-storage capability with a provider selector.

This one node replaces the shape a platform usually grows into:

    GoogleDriveListNode   GoogleDriveGetNode   GoogleDriveSearchNode
    OneDriveListNode      OneDriveGetNode      OneDriveSearchNode   …

The author picks a connection, a provider, and an operation. Provider
differences live in adapters beneath the node contract
(app/integrations/files/) — adding Dropbox/Box/SharePoint later is a new
adapter and a new preset, never a new node type.

The workflow references a *connection id*. It never contains a token, so a
workflow can be exported, versioned and shared without leaking account access.
File content never enters workflow state directly — get_file downloads
bytes, writes them to object storage, and returns a WorkflowFileRef pointer —
the SAME contract app/nodes/workflow_file_loader.py already consumes, not a
bespoke shape. That is deliberate: `{{outputs.<this node>.downloaded_file}}`
(or `downloaded_files` for several) can be wired straight into a
WorkflowFileLoader's `files` config, or any other node that accepts a
workflow file reference, exactly like an uploaded file would be.

`file_id`/`folder_id` accept either one id or a list of ids — a multi-select
in the Builder's file browser, or a mapped list of ids from an upstream
step, produces a list; select_file/select_folder/get_file all act on every
id given, and the outputs (`files`/`selected files`/`downloaded_files`) are
always the full collection, with `file`/`first`/`downloaded_file` as
first-of-the-collection convenience for the common single-id case.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.integrations.files.base import (
    CloudFileMeta,
    DownloadedFile,
    IntegrationAuthError,
    IntegrationNotFoundError,
)
from app.integrations.files.service import IntegrationConnectionError
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.runtime.schema import WorkflowFileRef
from app.workflow.file_inputs import (
    WorkflowFileInputError,
    category_for_extension,
    content_type_for,
    extension_for,
    safe_filename,
    TEXT_EXTRACTABLE_EXTENSIONS,
)

log = get_logger(__name__)

IntegrationProviderName = Literal["google_drive", "onedrive"]
IntegrationOperationName = Literal[
    "list_folder", "search_files", "select_file", "select_folder", "get_file"
]

PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "id": "google_drive",
        "label": "Google Drive",
        "summary": "Browse and pull files from a connected Google Drive account.",
        "config": {"provider": "google_drive"},
        "external_action": False,
    },
    {
        "id": "onedrive",
        "label": "OneDrive",
        "summary": "Browse and pull files from a connected OneDrive account.",
        "config": {"provider": "onedrive"},
        "external_action": False,
    },
]


class IntegrationNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Which cloud-storage provider this step reads from.
    provider: IntegrationProviderName = Field(
        description="Which cloud-storage provider this step reads from."
    )
    #: Named connection resolved by IntegrationService. Never a token.
    connection: str = Field(description="Which configured account connection this step acts against.")
    operation: IntegrationOperationName = Field(
        default="list_folder",
        description="The file operation to perform: list_folder/search_files browse; select_file/select_folder/get_file target one or more items.",
    )

    #: list_folder/search_files: scopes to exactly one folder (root when
    #: empty). select_folder: one or more folders to select.
    folder_id: str | list[str] | None = Field(
        default=None,
        description="Folder(s) to list, to scope a search to, or to select — root when empty for list_folder/search_files.",
    )
    #: select_file/get_file: one or more files to act on.
    file_id: str | list[str] | None = Field(
        default=None, description="File(s) to select or download."
    )
    query: str = Field(default="", description="Search text, in search_files.")
    page_size: int = Field(default=25, ge=1, le=200)
    page_token: str | None = Field(default=None, description="Opaque pagination cursor from a previous page.")

    @model_validator(mode="after")
    def operation_has_what_it_needs(self) -> "IntegrationNodeConfig":
        # Templated values are substituted before run(), so a config that
        # maps `file_id: "{{outputs.find.first.id}}"` looks present here and
        # is validated on its real value at runtime. This checks the author
        # supplied *something*, which is the mistake worth catching in the
        # Builder. `not value` is correctly falsy for None, "", and [].
        if self.operation in ("select_file", "get_file") and not self.file_id:
            raise ValueError(
                f"{self.operation} needs a file_id — map it from a "
                "list_folder/search_files step"
            )
        if self.operation == "select_folder" and not self.folder_id:
            raise ValueError("select_folder needs a folder_id")
        if self.operation == "search_files" and not self.query:
            raise ValueError("search_files needs a query")
        return self


class IntegrationNodeInput(BaseModel):
    pass


class CloudFileMetaOut(BaseModel):
    """The file shape downstream nodes address — flattened so a mapping
    reads `files.items.name` rather than a nested provider object."""

    id: str
    name: str
    mime_type: str = ""
    is_folder: bool = False
    size_bytes: int | None = None
    modified_at: str | None = None
    web_url: str | None = None
    parent_id: str | None = None


class IntegrationNodeOutput(BaseModel):
    operation: str = ""
    provider: str = ""
    connection: str = ""
    status: str = "ok"  # ok | error | not_connected | reauth_required
    #: list_folder/search_files results, OR every item select_file/
    #: select_folder acted on (one entry per id given).
    files: list[CloudFileMetaOut] = Field(default_factory=list)
    #: Convenience: files[0], for the common single-id case.
    file: CloudFileMetaOut | None = None
    #: Mirrors MCPToolAgent's found/not-found convenience field so
    #: `{{outputs.<node>.first.<field>}}` reads the same way everywhere.
    first: CloudFileMetaOut | None = None
    count: int = 0
    found: bool = False
    #: get_file only — a WorkflowFileRef pointer (bytes live in object
    #: storage), the SAME shape WorkflowFileLoader and any other
    #: file-consuming node already accepts. downloaded_file is
    #: downloaded_files[0], for the common single-file case.
    downloaded_file: WorkflowFileRef | None = None
    downloaded_files: list[WorkflowFileRef] = Field(default_factory=list)
    next_page_token: str | None = None
    error: str | None = None
    error_code: str | None = None  # not_connected | reauth_required | not_found | provider_error | unsupported_file_type
    retryable: bool = False


@NodeRegistry.register
class IntegrationAgent(NodeType):
    type_name = "IntegrationAgent"
    description = (
        "Browse and pull files from a connected Google Drive or OneDrive "
        "account — provider differences live in adapters."
    )
    input_schema = IntegrationNodeInput
    output_schema = IntegrationNodeOutput
    config_schema = IntegrationNodeConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "external"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Browses, searches, and downloads files from a configured cloud "
            "storage connection. Every operation is read-only."
        ),
        "why": (
            "One capability with a provider and operation selector, instead "
            "of a node type per cloud-storage vendor. Which account and "
            "which credentials is a deployment decision, not workflow content."
        ),
        "receives": "A folder/file id (or several) or a search query — usually mapped from an upstream step or a picker default.",
        "produces": "files/file metadata for browsing/selecting; downloaded_file(s) (workflow file references — wire straight into WorkflowFileLoader) for get_file.",
        "uses_ai": False,
        "external_action": True,
        "presets": PROVIDER_PRESETS,
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"files_integration", "object_store"}

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        declared = set(IntegrationNodeOutput.model_fields)
        meta_fields = set(CloudFileMetaOut.model_fields)
        ref_fields = set(WorkflowFileRef.model_fields)
        return (
            declared
            | {f"file.{name}" for name in meta_fields}
            | {f"first.{name}" for name in meta_fields}
            | {f"files.items.{name}" for name in meta_fields}
            | {f"downloaded_file.{name}" for name in ref_fields}
            | {f"downloaded_files.items.{name}" for name in ref_fields}
        )

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = IntegrationNodeConfig(**resolved_config)
        service = self.services.get("files_integration")
        store = self.services.get("object_store")
        if service is None:
            return _error_output(
                cfg,
                status="not_connected",
                error_code="not_connected",
                error=(
                    f"IntegrationAgent '{self.node_id}' needs the file-integration "
                    "service. No cloud-storage connection is configured in this deployment."
                ),
            )

        try:
            result = await service.execute(
                connection_id=cfg.connection,
                operation=cfg.operation,
                folder_id=cfg.folder_id,
                file_id=cfg.file_id,
                query=cfg.query,
                page_size=cfg.page_size,
                page_token=cfg.page_token,
            )
        except IntegrationAuthError as error:
            log.warning("integration.reauth_required", node_id=self.node_id, error=str(error))
            return _error_output(
                cfg, status="reauth_required", error_code="reauth_required",
                error=str(error), retryable=False,
            )
        except IntegrationNotFoundError as error:
            return _error_output(
                cfg, status="error", error_code="not_found", error=str(error), retryable=False,
            )
        except IntegrationConnectionError as error:
            return _error_output(
                cfg, status="not_connected", error_code="not_connected",
                error=str(error), retryable=False,
            )
        except Exception as error:
            log.warning("integration.provider_error", node_id=self.node_id, error=str(error))
            return _error_output(
                cfg, status="error", error_code="provider_error", error=str(error), retryable=True,
            )

        files_meta = result.files or result.selected_files
        files_out = [_meta_out(item) for item in files_meta]
        first = files_out[0] if files_out else None

        downloaded_refs: list[WorkflowFileRef] = []
        if result.downloaded_list:
            if store is None:
                return _error_output(
                    cfg, status="error", error_code="provider_error",
                    error="get_file downloaded content but no object_store service is configured.",
                    retryable=False,
                )
            for downloaded in result.downloaded_list:
                try:
                    ref = _to_workflow_file_ref(downloaded)
                except WorkflowFileInputError as error:
                    return _error_output(
                        cfg, status="error", error_code="unsupported_file_type",
                        error=str(error), retryable=False,
                    )
                await asyncio.to_thread(
                    store.put_bytes, downloaded.content, ref.minio_key, ref.content_type
                )
                downloaded_refs.append(ref)

        return {
            "operation": result.operation,
            "provider": result.provider,
            "connection": result.connection_id,
            "status": "ok",
            "files": [item.model_dump() for item in files_out],
            "file": files_out[0].model_dump() if files_out else None,
            "first": first.model_dump() if first else None,
            "count": len(files_out),
            "found": bool(files_out),
            "downloaded_file": downloaded_refs[0].model_dump() if downloaded_refs else None,
            "downloaded_files": [ref.model_dump() for ref in downloaded_refs],
            "next_page_token": result.next_page_token,
            "error": None,
            "error_code": None,
            "retryable": False,
        }


def _meta_out(meta: CloudFileMeta) -> CloudFileMetaOut:
    return CloudFileMetaOut(
        id=meta.id,
        name=meta.name,
        mime_type=meta.mime_type,
        is_folder=meta.is_folder,
        size_bytes=meta.size_bytes,
        modified_at=meta.modified_at.isoformat() if meta.modified_at else None,
        web_url=meta.web_url,
        parent_id=meta.parent_id,
    )


def _to_workflow_file_ref(downloaded: DownloadedFile) -> WorkflowFileRef:
    """Turn a downloaded cloud file into the platform's own file contract.

    Mirrors app/workflow/file_inputs.py's store_upload() exactly (same
    category/parseable_text classification, same content-addressed key
    shape) so a Drive/OneDrive download is indistinguishable, downstream,
    from a file a person uploaded through the Builder — WorkflowFileLoader
    and any other file-consuming node accept it with no special-casing.
    """
    name = safe_filename(downloaded.meta.name)
    extension = extension_for(name)
    category = category_for_extension(extension)  # raises WorkflowFileInputError if unsupported
    digest = hashlib.sha256(downloaded.content).hexdigest()
    content_type = downloaded.content_type or content_type_for(name, extension)
    return WorkflowFileRef(
        file_id=f"wf_{digest[:24]}",
        name=name,
        extension=extension,
        category=category,
        content_type=content_type,
        size_bytes=len(downloaded.content),
        sha256=digest,
        minio_key=f"integration/sha256:{digest}{extension}",
        parseable_text=extension in TEXT_EXTRACTABLE_EXTENSIONS,
    )


def _error_output(
    cfg: IntegrationNodeConfig,
    *,
    status: str,
    error_code: str,
    error: str,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "operation": cfg.operation,
        "provider": cfg.provider,
        "connection": cfg.connection,
        "status": status,
        "files": [],
        "file": None,
        "first": None,
        "count": 0,
        "found": False,
        "downloaded_file": None,
        "downloaded_files": [],
        "next_page_token": None,
        "error": error[:800],
        "error_code": error_code,
        "retryable": retryable,
    }
