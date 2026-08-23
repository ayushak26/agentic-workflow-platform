"""Prompt template library APIs for Business Chat."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.prompt_template_store import (
    PromptCategory, PromptTemplateStore, built_in_templates,
)

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])


def _scope(user: CurrentUser) -> str:
    return getattr(user, "session_id", None) or user.username


def _store(request: Request) -> PromptTemplateStore:
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Prompt template storage is unavailable")
    return PromptTemplateStore(db)


class TemplateBody(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    category: PromptCategory
    content: str = Field(min_length=1, max_length=50_000)


class FavoriteBody(BaseModel):
    favorite: bool


@router.get("")
async def list_prompt_templates(request: Request, user: CurrentUser = Depends(require_consultant)):
    custom = await _store(request).list(_scope(user))
    return {"templates": [*built_in_templates(), *(item.public() for item in custom)]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_prompt_template(body: TemplateBody, request: Request, user: CurrentUser = Depends(require_consultant)):
    return (await _store(request).create(_scope(user), **body.model_dump())).public()


@router.put("/{template_id}")
async def update_prompt_template(template_id: str, body: TemplateBody, request: Request, user: CurrentUser = Depends(require_consultant)):
    if template_id.startswith("builtin_"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in templates are immutable; duplicate it first")
    item = await _store(request).update(_scope(user), template_id, **body.model_dump())
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt template not found")
    return item.public()


@router.post("/{template_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_prompt_template(template_id: str, request: Request, user: CurrentUser = Depends(require_consultant)):
    source: dict[str, Any] | None = next((item for item in built_in_templates() if item["id"] == template_id), None)
    if source is None:
        record = await _store(request).get(_scope(user), template_id)
        source = record.public() if record else None
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt template not found")
    created = await _store(request).create(
        _scope(user), title=f"{source['title']} copy", description=source["description"],
        category=source["category"], content=source["content"],
    )
    return created.public()


@router.put("/{template_id}/favorite")
async def favorite_prompt_template(template_id: str, body: FavoriteBody, request: Request, user: CurrentUser = Depends(require_consultant)):
    if template_id.startswith("builtin_"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Duplicate a built-in template to favorite it")
    item = await _store(request).update(_scope(user), template_id, favorite=body.favorite)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt template not found")
    return item.public()


@router.delete("/{template_id}")
async def delete_prompt_template(template_id: str, request: Request, user: CurrentUser = Depends(require_consultant)):
    if template_id.startswith("builtin_"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in templates cannot be deleted")
    if not await _store(request).delete(_scope(user), template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt template not found")
    return {"id": template_id, "deleted": True}