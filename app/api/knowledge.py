"""Knowledge Studio collection, profile, index, document and ingestion APIs."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.config import settings
from app.ingestion.embedding_catalog import (
    AUTO_EMBEDDING_MODEL,
    EMBEDDING_MODELS_BY_ID,
    embedding_model_catalog,
    select_embedding_model,
)
from app.knowledge.doc_types import doc_type_catalog
from app.knowledge.ids import new_resource_id
from app.knowledge.models import (
    IngestionJob,
    IngestionJobStatus,
    IngestionSourceInput,
    ProfileType,
    ResourceStatus,
)
from app.knowledge.repository import ResourceConflictError, ResourceNotFoundError
from app.knowledge.service import workspace_for_scope
from app.security.dependencies import CurrentUser, require_permission
from app.storage.minio_client import content_hash, knowledge_key_for_path
from app.retrieval.filters import MetadataFilterValidationError, validate_metadata_document

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _scope(user: CurrentUser) -> str:
    return user.session_id or user.username


def _service(request: Request):
    service = request.app.state.services.get("knowledge_service")
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Knowledge service unavailable")
    return service


def _repository(request: Request):
    repository = request.app.state.services.get("knowledge_repository")
    if repository is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Knowledge repository unavailable")
    return repository


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    metadata_schema: dict[str, Any] = Field(default_factory=dict)
    doc_types: list[str] = Field(default_factory=lambda: ["general"])


@router.post("/collections", status_code=status.HTTP_201_CREATED)
async def create_collection(
    payload: CollectionCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    return await _service(request).create_collection(
        owner_scope_id=_scope(user), **payload.model_dump()
    )


@router.get("/collections")
async def list_collections(
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    return await _repository(request).list_collections(_scope(user))


@router.get("/collections/{collection_id}")
async def get_collection(
    collection_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    try:
        return await _repository(request).get_collection(_scope(user), collection_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


class ProfileCreate(BaseModel):
    profile_type: ProfileType
    name: str
    strategy: str
    config: dict[str, Any] = Field(default_factory=dict)
    profile_id: str | None = None
    description: str = ""
    based_on_preset: str | None = None


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: ProfileCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    try:
        return await _service(request).create_profile_version(
            owner_scope_id=_scope(user), **payload.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/profiles")
async def list_profiles(
    request: Request,
    profile_type: ProfileType | None = Query(None),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    return await _repository(request).list_profiles(_scope(user), profile_type)


@router.get("/profiles/{profile_id}")
async def get_profile(
    profile_id: str,
    request: Request,
    version: int | None = Query(None, ge=1),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    try:
        return await _repository(request).get_profile(_scope(user), profile_id, version)
    except ResourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/profiles/defaults")
async def create_default_profiles(
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    return await _service(request).ensure_default_profiles(_scope(user))


INGESTION_PRESETS: dict[str, dict[str, Any]] = {
    "general_documents": {
        "name": "General Documents",
        "parser": {"strategy": "standard", "config": {}},
        "chunking": {"strategy": "recursive", "config": {}},
        "enrichment": {"prepend_context": False},
    },
    "technical_documentation": {
        "name": "Technical Documentation",
        "parser": {"strategy": "layout_aware", "config": {}},
        "chunking": {"strategy": "parent_child", "config": {"target_tokens": 420, "parent_tokens": 1600}},
        "enrichment": {"prepend_context": True},
    },
    "policies_contracts": {
        "name": "Policies / Contracts",
        "parser": {"strategy": "layout_aware", "config": {}},
        "chunking": {"strategy": "structure_aware", "config": {"target_tokens": 650, "max_tokens": 1200}},
        "enrichment": {"prepend_context": True},
    },
    "fast_demo": {
        "name": "Fast Demo",
        "parser": {"strategy": "standard", "config": {}},
        "chunking": {"strategy": "recursive", "config": {"target_tokens": 384, "max_tokens": 768}},
        "enrichment": {"prepend_context": False},
    },
}


@router.get("/ingestion-presets")
async def ingestion_presets(
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    return INGESTION_PRESETS


@router.get("/doc-types")
async def doc_types(
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    """Document types a Collection may declare.

    An open vocabulary — a Collection may use a value not listed here — but
    these are the ones the platform actually reads, so the picker offers them.
    """
    return {"doc_types": doc_type_catalog()}


@router.get("/embedding-models")
async def embedding_models(
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    """Embedding models selectable when building an Index.

    Served from the backend so the UI never hardcodes model ids or dimension
    counts — both are pinned into the Embedding Profile and an Index Version.
    """
    return {
        "models": embedding_model_catalog(),
        "configured_default": settings.embedding_model,
        "endpoint": settings.embedding_base_url or "https://api.openai.com/v1",
    }


class IndexCreate(BaseModel):
    parser_profile_id: str
    parser_profile_version: int = Field(ge=1)
    chunking_profile_id: str
    chunking_profile_version: int = Field(ge=1)
    embedding_profile_id: str
    embedding_profile_version: int = Field(ge=1)


@router.post("/collections/{collection_id}/indexes", status_code=status.HTTP_201_CREATED)
async def create_index(
    collection_id: str,
    payload: IndexCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    try:
        return await _service(request).create_index(
            owner_scope_id=_scope(user), collection_id=collection_id, **payload.model_dump()
        )
    except (ResourceNotFoundError, ResourceConflictError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/collections/{collection_id}/indexes")
async def list_indexes(
    collection_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    await _repository(request).get_collection(_scope(user), collection_id)
    return await _repository(request).list_indexes(_scope(user), collection_id)


@router.post("/collections/{collection_id}/indexes/{index_id}/activate")
async def activate_index(
    collection_id: str,
    index_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    try:
        return await _service(request).activate_index(
            owner_scope_id=_scope(user), collection_id=collection_id, index_id=index_id
        )
    except (ResourceNotFoundError, ResourceConflictError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/collections/{collection_id}/ingestions", status_code=status.HTTP_202_ACCEPTED)
async def start_ingestion(
    collection_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    parser_profile_id: str | None = Form(None),
    parser_profile_version: int | None = Form(None),
    chunking_profile_id: str | None = Form(None),
    chunking_profile_version: int | None = Form(None),
    embedding_profile_id: str | None = Form(None),
    embedding_profile_version: int | None = Form(None),
    embedding_model: str | None = Form(None),
    metadata_json: str = Form("{}"),
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    scope = _scope(user)
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At least one file is required")
    if len(files) > settings.workflow_file_max_files:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"At most {settings.workflow_file_max_files} files may be uploaded",
        )
    service = _service(request)
    repository = _repository(request)
    collection = await repository.get_collection(scope, collection_id)
    defaults = await service.ensure_default_profiles(scope)
    parser = await repository.get_profile(
        scope,
        parser_profile_id or defaults["parser"].profile_id,
        parser_profile_version or (None if parser_profile_id else defaults["parser"].version),
        ProfileType.PARSER,
    )
    chunking = await repository.get_profile(
        scope,
        chunking_profile_id or defaults["chunking"].profile_id,
        chunking_profile_version or (None if chunking_profile_id else defaults["chunking"].version),
        ProfileType.CHUNKING,
    )
    if embedding_profile_id:
        embedding = await repository.get_profile(
            scope, embedding_profile_id, embedding_profile_version, ProfileType.EMBEDDING
        )
    elif embedding_model:
        # Resolve here, never at storage time: an Index Version must pin a
        # concrete model and dimension count, so "auto" is decided once, on
        # the corpus in hand, and the reason is recorded on the profile.
        if embedding_model == AUTO_EMBEDDING_MODEL:
            sample = " ".join((upload.filename or "") for upload in files)
            choice, reason = select_embedding_model(
                doc_types=collection.doc_types,
                document_count=len(files),
                total_bytes=sum(getattr(upload, "size", 0) or 0 for upload in files),
                sample_text=sample,
            )
        else:
            choice = EMBEDDING_MODELS_BY_ID.get(embedding_model)
            if choice is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"unknown embedding model {embedding_model!r}",
                )
            reason = "explicitly selected"
        embedding = await service.create_profile_version(
            owner_scope_id=scope,
            profile_type=ProfileType.EMBEDDING,
            name=f"Embeddings {choice.id}",
            strategy="openai",
            config={
                "provider": choice.provider,
                "model": choice.id,
                "dimensions": choice.dimensions,
                "batch_size": 64,
                "data_processing": "external",
            },
            description=f"{choice.label} — {reason}",
        )
    else:
        embedding = await repository.get_profile(
            scope,
            defaults["embedding"].profile_id,
            defaults["embedding"].version,
            ProfileType.EMBEDDING,
        )
    try:
        metadata = json.loads(metadata_json)
        if not isinstance(metadata, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "metadata_json must be an object") from exc
    try:
        validate_metadata_document(metadata, collection.metadata_schema)
    except MetadataFilterValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    index = await service.create_index(
        owner_scope_id=scope,
        collection_id=collection.collection_id,
        parser_profile_id=parser.profile_id,
        parser_profile_version=parser.version,
        chunking_profile_id=chunking.profile_id,
        chunking_profile_version=chunking.version,
        embedding_profile_id=embedding.profile_id,
        embedding_profile_version=embedding.version,
    )
    directory = Path(tempfile.mkdtemp(prefix="eurskem-ingestion-"))
    paths: list[Path] = []
    total_bytes = 0
    seen_names: set[str] = set()
    try:
        for upload in files:
            safe_name = Path(upload.filename or "document").name
            if safe_name in seen_names:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Duplicate upload filename: {safe_name}",
                )
            seen_names.add(safe_name)
            destination = directory / safe_name
            file_bytes = 0
            with destination.open("wb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > settings.workflow_file_max_bytes:
                        raise HTTPException(
                            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"{safe_name} exceeds the {settings.workflow_file_max_mb} MB per-file limit",
                        )
                    if total_bytes > settings.workflow_file_max_total_bytes:
                        raise HTTPException(
                            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Uploads exceed the {settings.workflow_file_max_total_mb} MB total limit",
                        )
                    handle.write(chunk)
            paths.append(destination)
            await upload.close()
    except Exception:
        index.status = ResourceStatus.FAILED
        await repository.save_index(index)
        await asyncio.to_thread(shutil.rmtree, directory, True)
        for upload in files:
            await upload.close()
        raise
    services = request.app.state.services
    source_inputs: list[IngestionSourceInput] = []
    try:
        for path in paths:
            storage_key = knowledge_key_for_path(path, scope)
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            digest = await asyncio.to_thread(content_hash, path)
            await asyncio.to_thread(
                services["object_store"].put_file,
                path,
                storage_key,
                mime_type,
                {"orig_filename": path.name, "ingestion_staged": "true"},
            )
            source_inputs.append(
                IngestionSourceInput(
                    filename=path.name,
                    storage_key=storage_key,
                    mime_type=mime_type,
                    content_hash=digest,
                    byte_size=path.stat().st_size,
                )
            )
    except Exception:
        index.status = ResourceStatus.FAILED
        await repository.save_index(index)
        raise
    finally:
        await asyncio.to_thread(shutil.rmtree, directory, True)
    job = IngestionJob(
        ingestion_job_id=new_resource_id("ingestion_job"),
        workspace_id=collection.workspace_id,
        owner_scope_id=scope,
        collection_id=collection.collection_id,
        parser_profile_id=parser.profile_id,
        parser_profile_version=parser.version,
        chunking_profile_id=chunking.profile_id,
        chunking_profile_version=chunking.version,
        embedding_profile_id=embedding.profile_id,
        embedding_profile_version=embedding.version,
        target_index_id=index.index_id,
        documents_total=len(paths),
        source_inputs=source_inputs,
        metadata=metadata,
    )
    await repository.save_ingestion_job(job)
    coordinator = services.get("ingestion_coordinator")
    if coordinator is None:
        job.status = IngestionJobStatus.FAILED
        job.errors.append({"error_type": "CoordinatorUnavailable", "message": "Ingestion coordinator is unavailable"})
        await repository.save_ingestion_job(job)
        index.status = ResourceStatus.FAILED
        await repository.save_index(index)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Ingestion coordinator is unavailable",
        )
    coordinator.submit(job)
    return job


@router.get("/ingestions")
async def list_ingestions(
    request: Request,
    collection_id: str | None = Query(None),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    return await _repository(request).list_ingestion_jobs(_scope(user), collection_id)


@router.get("/ingestions/{ingestion_job_id}")
async def get_ingestion(
    ingestion_job_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    try:
        return await _repository(request).get_ingestion_job(_scope(user), ingestion_job_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/ingestions/{ingestion_job_id}/cancel")
async def cancel_ingestion(
    ingestion_job_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    repository = _repository(request)
    job = await repository.get_ingestion_job(_scope(user), ingestion_job_id)
    if job.status in {
        IngestionJobStatus.COMPLETED,
        IngestionJobStatus.PARTIALLY_COMPLETED,
        IngestionJobStatus.FAILED,
        IngestionJobStatus.CANCELLED,
    }:
        raise HTTPException(status.HTTP_409_CONFLICT, "ingestion job is already terminal")
    job.status = IngestionJobStatus.CANCELLED
    from datetime import datetime, timezone
    job.cancelled_at = datetime.now(timezone.utc)
    return await repository.save_ingestion_job(job)


@router.get("/collections/{collection_id}/documents")
async def list_documents(
    collection_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    await _repository(request).get_collection(_scope(user), collection_id)
    return await _repository(request).list_documents(_scope(user), collection_id)


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    try:
        return await _repository(request).get_document(_scope(user), document_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/documents/{document_id}/source-url")
async def document_source_url(
    document_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    repository = _repository(request)
    document = await repository.get_document(_scope(user), document_id)
    if not document.current_source_version_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document has no source version")
    source = await repository.get_source_version(_scope(user), document.current_source_version_id)
    url = request.app.state.services["object_store"].presigned_url(source.storage_key, 600)
    return {"document_id": document_id, "source_version_id": source.source_version_id, "url": url, "expires_seconds": 600}
