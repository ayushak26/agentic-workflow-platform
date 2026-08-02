"""Manual entity-registry CRUD — Phase 1 confidential entity protection.

Lets a consultant pre-register known partner/client/coordinator/acronym
names before a run, so the registry (the primary detection mechanism) is
populated ahead of time rather than relying solely on the regex/NER safety
net. No frontend ships with Phase 1 — this is the backend surface for one.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.security.dependencies import CurrentUser, require_consultant
from app.security.entity_protection_errors import (
    EntityVaultUnavailableError,
    VaultKeyMisconfiguredError,
)
from app.security.entity_registry import ENTITY_TYPES

router = APIRouter(prefix="/api/entity-registry", tags=["entity-registry"])


def _scope(user: CurrentUser) -> str:
    return getattr(user, "session_id", None) or user.username


def _service(request: Request):
    service = getattr(request.app.state, "services", {}).get("entity_tokenizer")
    if service is None:
        raise HTTPException(status_code=503, detail="entity registry unavailable")
    return service


class RegisterEntityRequest(BaseModel):
    entity_type: str
    value: str
    collection_id: str = "default"


class DeleteEntityRequest(BaseModel):
    entity_type: str
    value: str
    collection_id: str = "default"


@router.get("/entity-types")
def list_entity_types() -> dict[str, list[str]]:
    return {"entity_types": sorted(ENTITY_TYPES)}


@router.get("")
async def list_entities(
    request: Request,
    collection_id: str = "default",
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    service = _service(request)
    try:
        entities = await service.registry.list_entities(
            session_id=_scope(user), collection_id=collection_id
        )
    except (EntityVaultUnavailableError, VaultKeyMisconfiguredError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"entities": entities}


@router.post("")
async def register_entity(
    req: RegisterEntityRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, str]:
    service = _service(request)
    try:
        placeholder = await service.registry.register(
            session_id=_scope(user),
            collection_id=req.collection_id,
            entity_type=req.entity_type,
            value=req.value,
            source="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (EntityVaultUnavailableError, VaultKeyMisconfiguredError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"placeholder": placeholder}


@router.delete("")
async def delete_entity(
    req: DeleteEntityRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, bool]:
    service = _service(request)
    try:
        deleted = await service.registry.delete(
            session_id=_scope(user),
            collection_id=req.collection_id,
            entity_type=req.entity_type,
            value=req.value,
        )
    except (EntityVaultUnavailableError, VaultKeyMisconfiguredError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"deleted": deleted}
