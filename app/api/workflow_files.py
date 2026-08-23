"""Authenticated multipart upload/download API for workflow input files."""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import settings
from app.runtime.schema import FILE_INPUT_CATEGORIES, WorkflowFileRef
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.file_inputs import (
    ALL_WORKFLOW_FILE_EXTENSIONS,
    FILE_CATEGORY_EXTENSIONS,
    REFERENCE_ONLY_EXTENSIONS,
    TEXT_EXTRACTABLE_EXTENSIONS,
    WorkflowFileInputError,
    extract_workflow_file_text,
    record_uploaded_files,
    store_upload,
    workflow_input_prefix,
)

router = APIRouter(
    prefix="/api/workflow-input-files",
    tags=["workflow-input-files"],
)


def _scope(user: CurrentUser) -> str:
    """Internal helper for the scope step.

    Args:
        user (CurrentUser): Authenticated current user.

    Returns:
        str: The result.
    """
    return getattr(user, "session_id", None) or user.username


@router.get("/capabilities")
async def capabilities(
    _user: CurrentUser = Depends(require_consultant),
):
    """Return the authoritative UI picker limits and accepted extensions."""

    return {
        "categories": {
            category: list(FILE_CATEGORY_EXTENSIONS[category])
            for category in FILE_INPUT_CATEGORIES
        },
        "extensions": list(ALL_WORKFLOW_FILE_EXTENSIONS),
        "extractable_extensions": list(TEXT_EXTRACTABLE_EXTENSIONS),
        "reference_only_extensions": list(REFERENCE_ONLY_EXTENSIONS),
        "max_file_size_bytes": settings.workflow_file_max_bytes,
        "max_files_per_input": settings.workflow_file_max_files,
    }


@router.post("")
async def upload_workflow_input_files(
    files: Annotated[list[UploadFile], File(...)],
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Store files and return stable references suitable for workflow state."""

    if not files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "At least one file is required",
        )
    if len(files) > settings.workflow_file_max_files:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"At most {settings.workflow_file_max_files} files may be uploaded",
        )

    services = getattr(request.app.state, "services", {})
    object_store = services.get("object_store")
    if object_store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Object storage is unavailable",
        )

    session_id = _scope(user)
    uploaded: list[WorkflowFileRef] = []
    remaining = settings.workflow_file_max_total_bytes
    try:
        for file in files:
            ref = await store_upload(
                file,
                session_id=session_id,
                object_store=object_store,
                remaining_total_bytes=remaining,
            )
            uploaded.append(ref)
            remaining -= ref.size_bytes
        await record_uploaded_files(
            services.get("audit_db"),
            session_id=session_id,
            refs=uploaded,
        )
    except WorkflowFileInputError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(exc),
        ) from exc
    except Exception as exc:
        from app.observability.logging import get_logger

        get_logger(__name__).warning(
            "workflow_file.upload_failed",
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "File upload failed",
        ) from exc

    return {"files": [ref.model_dump() for ref in uploaded]}


class ExtractWorkflowFileRequest(BaseModel):
    """Pydantic model defining the ExtractWorkflowFileRequest shape.

    Attributes:
        file (WorkflowFileRef).
        max_chars (int).
    """
    file: WorkflowFileRef
    max_chars: int = Field(default=1_000_000, ge=1_000, le=2_000_000)


@router.post("/extract")
async def extract_workflow_input_file(
    body: ExtractWorkflowFileRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Convert one uploaded document into plain text for the HITL editor."""

    services = getattr(request.app.state, "services", {})
    try:
        return await extract_workflow_file_text(
            body.file,
            session_id=_scope(user),
            object_store=services.get("object_store"),
            max_chars=body.max_chars,
        )
    except WorkflowFileInputError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(exc),
        ) from exc


@router.get("/content")
async def download_workflow_input_file(
    key: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Download one uploaded input after enforcing the authenticated scope."""

    session_id = _scope(user)
    if not key.startswith(workflow_input_prefix(session_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    services = getattr(request.app.state, "services", {})
    object_store = services.get("object_store")
    if object_store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Object storage is unavailable",
        )
    db = services.get("audit_db")
    record = None
    if db is not None:
        record = await db["workflow_input_files"].find_one(
            {"_id": key, "session_id": session_id}
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    try:
        data = await asyncio.to_thread(object_store.get_bytes, key)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "File not found",
        ) from exc

    filename = str((record or {}).get("name") or key.rsplit("/", 1)[-1])
    media_type = str(
        (record or {}).get("content_type") or "application/octet-stream"
    )
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
