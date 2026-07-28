"""Workflow file-input contracts, validation, and object-storage helpers."""
from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from pydantic import ValidationError

from app.config import settings
from app.ingestion.extractor import get_extractor, supported_extensions
from app.runtime.schema import WorkflowFileRef, WorkflowInputSpec


FILE_CATEGORY_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "document": (".docx", ".txt"),
    "markdown": (".md", ".markdown"),
    "presentation": (".pptx",),
    "spreadsheet": (".xlsx",),
    "code": (
        ".c", ".cfg", ".cpp", ".cs", ".css", ".go", ".h", ".hpp",
        ".html", ".ini", ".ipynb", ".java", ".js", ".json", ".jsx",
        ".kt", ".php", ".properties", ".py", ".r", ".rb", ".rs", ".scala",
        ".sh", ".sql", ".svelte", ".swift", ".tf", ".toml", ".ts", ".tsx",
        ".vue", ".xml", ".yaml", ".yml",
    ),
    "image": (
        ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".svg", ".tif",
        ".tiff", ".webp",
    ),
}

EXTENSION_CATEGORY = {
    extension: category
    for category, extensions in FILE_CATEGORY_EXTENSIONS.items()
    for extension in extensions
}

ALL_WORKFLOW_FILE_EXTENSIONS = tuple(sorted(EXTENSION_CATEGORY))
_TEXT_PARSEABLE_EXTENSIONS = frozenset(supported_extensions())
TEXT_EXTRACTABLE_EXTENSIONS = tuple(sorted(_TEXT_PARSEABLE_EXTENSIONS))
REFERENCE_ONLY_EXTENSIONS = tuple(
    sorted(set(FILE_CATEGORY_EXTENSIONS["image"]) - _TEXT_PARSEABLE_EXTENSIONS)
)
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


class WorkflowFileInputError(ValueError):
    """Raised when an upload or workflow file reference is invalid."""


def scope_token(session_id: str) -> str:
    """Return a non-reversible, path-safe tenant/session token."""

    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]


def workflow_input_prefix(session_id: str) -> str:
    return f"workflow-inputs/{scope_token(session_id)}/"


def safe_filename(filename: str | None) -> str:
    """Strip paths/control characters while retaining a readable name."""

    name = Path(filename or "upload").name.replace("\x00", "").strip()
    name = _SAFE_FILENAME.sub("_", name)
    return name[:180] or "upload"


def extension_for(filename: str) -> str:
    return Path(filename).suffix.lower()


def category_for_extension(extension: str) -> str:
    try:
        return EXTENSION_CATEGORY[extension.lower()]
    except KeyError as exc:
        raise WorkflowFileInputError(
            f"Unsupported file type {extension or '(no extension)'}. "
            f"Allowed extensions: {', '.join(ALL_WORKFLOW_FILE_EXTENSIONS)}"
        ) from exc


def content_type_for(filename: str, extension: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    if extension in _TEXT_PARSEABLE_EXTENSIONS:
        return "text/plain"
    return "application/octet-stream"


def accepted_by_spec(ref: WorkflowFileRef, spec: WorkflowInputSpec) -> bool:
    """Accept category names or literal extensions in workflow YAML."""

    accepts = {item.lower() for item in spec.accept}
    return ref.category.lower() in accepts or ref.extension.lower() in accepts


async def store_upload(
    upload: UploadFile,
    *,
    session_id: str,
    object_store: Any,
) -> WorkflowFileRef:
    """Stream one multipart upload to disk, enforce limits, then store it."""

    filename = safe_filename(upload.filename)
    extension = extension_for(filename)
    category = category_for_extension(extension)
    max_bytes = settings.workflow_file_max_bytes
    chunk_bytes = 1024 * 1024
    size = 0
    sha = hashlib.sha256()
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix="eurskem-workflow-input-",
            suffix=extension,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            while True:
                chunk = await upload.read(chunk_bytes)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise WorkflowFileInputError(
                        f"{filename} exceeds the "
                        f"{settings.workflow_file_max_mb} MB per-file limit"
                    )
                sha.update(chunk)
                temporary.write(chunk)

        digest = sha.hexdigest()
        key = f"{workflow_input_prefix(session_id)}{digest}{extension}"
        content_type = content_type_for(filename, extension)
        ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "upload"

        await asyncio.to_thread(
            object_store.put_file,
            temporary_path,
            key=key,
            content_type=content_type,
            extra_metadata={
                "orig_filename": ascii_name[:180],
                "scope": scope_token(session_id),
            },
        )
        return WorkflowFileRef(
            file_id=f"wf_{digest[:24]}",
            name=filename,
            extension=extension,
            category=category,
            content_type=content_type,
            size_bytes=size,
            sha256=digest,
            minio_key=key,
            parseable_text=extension in _TEXT_PARSEABLE_EXTENSIONS,
        )
    finally:
        await upload.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def validate_workflow_inputs(
    specs: dict[str, WorkflowInputSpec],
    values: dict[str, Any],
    *,
    session_id: str,
    object_store: Any | None,
) -> dict[str, Any]:
    """Validate and normalize declared workflow file inputs before execution."""

    normalized = dict(values)
    prefix = workflow_input_prefix(session_id)

    for input_name, spec in specs.items():
        value = values.get(input_name)
        absent = value is None or value == "" or value == []
        if absent:
            if spec.required:
                raise WorkflowFileInputError(
                    f"Required workflow input {input_name!r} is missing"
                )
            continue

        if spec.type != "file":
            continue
        if object_store is None:
            raise WorkflowFileInputError(
                "Object storage is unavailable; file inputs cannot be verified"
            )

        raw_refs = value if isinstance(value, list) else [value]
        limit = spec.effective_max_files(settings.workflow_file_max_files)
        if len(raw_refs) > limit:
            raise WorkflowFileInputError(
                f"{input_name!r} accepts at most {limit} file(s)"
            )

        refs: list[WorkflowFileRef] = []
        for raw_ref in raw_refs:
            try:
                ref = WorkflowFileRef.model_validate(raw_ref)
            except ValidationError as exc:
                raise WorkflowFileInputError(
                    f"{input_name!r} contains an invalid file reference"
                ) from exc
            if not ref.minio_key.startswith(prefix):
                raise WorkflowFileInputError(
                    f"{input_name!r} references a file outside this session"
                )
            if not accepted_by_spec(ref, spec):
                raise WorkflowFileInputError(
                    f"{ref.name} is not accepted by workflow input {input_name!r}"
                )
            exists = await asyncio.to_thread(
                object_store.object_exists,
                ref.minio_key,
            )
            if not exists:
                raise WorkflowFileInputError(
                    f"Uploaded file {ref.name!r} is no longer available"
                )
            refs.append(ref)

        normalized[input_name] = (
            [ref.model_dump() for ref in refs]
            if spec.multiple
            else refs[0].model_dump()
        )

    return normalized


async def validate_workflow_file_reference(
    raw_ref: WorkflowFileRef | dict[str, Any],
    *,
    session_id: str,
    object_store: Any | None,
    require_parseable_text: bool = False,
) -> WorkflowFileRef:
    """Validate one file reference before a download, extraction, or HITL edit."""

    try:
        ref = WorkflowFileRef.model_validate(raw_ref)
    except ValidationError as exc:
        raise WorkflowFileInputError(
            "Invalid workflow file reference"
        ) from exc
    if not session_id:
        raise WorkflowFileInputError("A session is required for file access")
    if not ref.minio_key.startswith(workflow_input_prefix(session_id)):
        raise WorkflowFileInputError("File reference is outside this session")
    if require_parseable_text and not ref.parseable_text:
        raise WorkflowFileInputError(
            f"{ref.name} cannot be converted to editable text"
        )
    if object_store is None:
        raise WorkflowFileInputError("Object storage is unavailable")
    exists = await asyncio.to_thread(
        object_store.object_exists,
        ref.minio_key,
    )
    if not exists:
        raise WorkflowFileInputError(
            f"Uploaded file {ref.name!r} is no longer available"
        )
    return ref


async def extract_workflow_file_text(
    raw_ref: WorkflowFileRef | dict[str, Any],
    *,
    session_id: str,
    object_store: Any | None,
    max_chars: int = 1_000_000,
) -> dict[str, Any]:
    """Extract one scoped upload into plain text for the HITL editor."""

    if max_chars < 1_000 or max_chars > 2_000_000:
        raise WorkflowFileInputError(
            "max_chars must be between 1,000 and 2,000,000"
        )
    ref = await validate_workflow_file_reference(
        raw_ref,
        session_id=session_id,
        object_store=object_store,
        require_parseable_text=True,
    )
    temporary_path: Path | None = None
    try:
        raw = await asyncio.to_thread(
            object_store.get_bytes,
            ref.minio_key,
        )
        with tempfile.NamedTemporaryFile(
            prefix="eurskem-hitl-override-",
            suffix=ref.extension,
            delete=False,
        ) as temporary:
            temporary.write(raw)
            temporary_path = Path(temporary.name)

        extractor = get_extractor(temporary_path)
        document = await asyncio.to_thread(
            extractor.extract,
            temporary_path,
        )
        full_text = document.full_text
        return {
            "file": ref.model_dump(),
            "text": full_text[:max_chars],
            "total_chars": len(full_text),
            "extracted_chars": min(len(full_text), max_chars),
            "truncated": len(full_text) > max_chars,
        }
    except WorkflowFileInputError:
        raise
    except Exception as exc:
        raise WorkflowFileInputError(
            f"Could not extract editable text from {ref.name}: {exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def record_uploaded_files(
    db: Any | None,
    *,
    session_id: str,
    refs: list[WorkflowFileRef],
) -> None:
    """Persist upload metadata for audit/cleanup without storing file bytes."""

    if db is None:
        return
    now = datetime.now(timezone.utc)
    for ref in refs:
        await db["workflow_input_files"].update_one(
            {"_id": ref.minio_key, "session_id": session_id},
            {
                "$set": {
                    **ref.model_dump(),
                    "session_id": session_id,
                    "updated_at": now,
                },
                "$setOnInsert": {"uploaded_at": now},
            },
            upsert=True,
        )
