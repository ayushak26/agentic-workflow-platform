"""Durable transcript APIs for Business Chat."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.chat_conversation_store import (
    ChatConversationNotFoundError,
    ChatConversationStore,
    ChatMessageRole,
    ChatWorkflowSource,
)


router = APIRouter(prefix="/api/chat-conversations", tags=["chat-conversations"])
_MAX_CONTENT_BYTES = 1_000_000


def _scope(user: CurrentUser) -> str:
    return getattr(user, "session_id", None) or user.username


def _store(request: Request) -> ChatConversationStore:
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Business Chat transcript storage is unavailable",
        )
    return ChatConversationStore(db)


class ResolveConversationBody(BaseModel):
    workflow_source: ChatWorkflowSource
    workflow_id: str = Field(min_length=1, max_length=500)


class MessageBody(BaseModel):
    message_id: str = Field(min_length=1, max_length=200)
    role: ChatMessageRole
    content: dict[str, Any]
    run_id: str | None = Field(default=None, max_length=200)

    @field_validator("content")
    @classmethod
    def content_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("message content must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise ValueError("message content exceeds the 1 MB limit")
        return value


class ReplaceMessageBody(BaseModel):
    role: ChatMessageRole
    content: dict[str, Any]
    run_id: str | None = Field(default=None, max_length=200)

    _content_is_bounded = field_validator("content")(
        MessageBody.content_is_bounded.__func__,
    )


@router.post("/resolve")
async def resolve_conversation(
    body: ResolveConversationBody,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    store = _store(request)
    conversation = await store.get_or_create(
        _scope(user),
        workflow_source=body.workflow_source,
        workflow_id=body.workflow_id,
    )
    messages = await store.list_messages(_scope(user), conversation.conversation_id)
    return {
        "conversation": conversation.public(),
        "messages": [message.public() for message in messages],
    }


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    store = _store(request)
    try:
        conversation = await store.get(_scope(user), conversation_id)
        messages = await store.list_messages(_scope(user), conversation_id)
    except ChatConversationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {
        "conversation": conversation.public(),
        "messages": [message.public() for message in messages],
    }


@router.post("/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def append_message(
    conversation_id: str,
    body: MessageBody,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    try:
        message = await _store(request).append_message(
            _scope(user), conversation_id, **body.model_dump(),
        )
    except ChatConversationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return message.public()


@router.put("/{conversation_id}/messages/{message_id}")
async def replace_message(
    conversation_id: str,
    message_id: str,
    body: ReplaceMessageBody,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    try:
        message = await _store(request).replace_message(
            _scope(user), conversation_id, message_id, **body.model_dump(),
        )
    except ChatConversationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return message.public()