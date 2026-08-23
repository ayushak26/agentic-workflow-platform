"""Durable, owner-scoped Business Chat conversations and transcript messages."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError


CHAT_CONVERSATIONS_COLLECTION = "chat_conversations"
CHAT_CONVERSATION_MESSAGES_COLLECTION = "chat_conversation_messages"
ChatWorkflowSource = Literal["shared", "private"]
ChatMessageRole = Literal["user", "assistant", "attempt", "error", "intervention"]


class ChatConversationNotFoundError(LookupError):
    """The conversation is not accessible in the authenticated owner scope."""


class ChatConversationRecord(BaseModel):
    conversation_id: str
    owner_scope_id: str
    workflow_source: ChatWorkflowSource
    workflow_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = 1

    def public(self) -> dict[str, Any]:
        return {
            "id": self.conversation_id,
            "workflow_source": self.workflow_source,
            "workflow_id": self.workflow_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ChatConversationMessageRecord(BaseModel):
    conversation_id: str
    owner_scope_id: str
    message_id: str
    role: ChatMessageRole
    content: dict[str, Any]
    run_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = 1

    def public(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "role": self.role,
            "content": self.content,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


async def ensure_chat_conversation_indexes(db: Any) -> None:
    conversations = db[CHAT_CONVERSATIONS_COLLECTION]
    messages = db[CHAT_CONVERSATION_MESSAGES_COLLECTION]
    await conversations.create_index("conversation_id", unique=True)
    await conversations.create_index(
        [("owner_scope_id", 1), ("workflow_source", 1), ("workflow_id", 1)],
        unique=True,
    )
    await messages.create_index(
        [("owner_scope_id", 1), ("conversation_id", 1), ("message_id", 1)],
        unique=True,
    )
    await messages.create_index(
        [("owner_scope_id", 1), ("conversation_id", 1), ("created_at", 1)],
    )


class ChatConversationStore:
    """Conversation persistence where every operation is owner-scoped."""

    def __init__(self, db: Any):
        self.conversations = db[CHAT_CONVERSATIONS_COLLECTION]
        self.messages = db[CHAT_CONVERSATION_MESSAGES_COLLECTION]

    async def get_or_create(
        self,
        owner_scope_id: str,
        *,
        workflow_source: ChatWorkflowSource,
        workflow_id: str,
    ) -> ChatConversationRecord:
        query = {
            "owner_scope_id": owner_scope_id,
            "workflow_source": workflow_source,
            "workflow_id": workflow_id,
        }
        existing = await self.conversations.find_one(query)
        if existing is not None:
            return ChatConversationRecord.model_validate(existing)
        record = ChatConversationRecord(
            conversation_id=f"chat_{uuid.uuid4().hex}",
            owner_scope_id=owner_scope_id,
            workflow_source=workflow_source,
            workflow_id=workflow_id,
        )
        try:
            await self.conversations.insert_one(record.model_dump(mode="python"))
            return record
        except DuplicateKeyError:
            # Another tab created the same owner/workflow conversation first.
            existing = await self.conversations.find_one(query)
            if existing is None:
                raise
            return ChatConversationRecord.model_validate(existing)

    async def get(
        self, owner_scope_id: str, conversation_id: str,
    ) -> ChatConversationRecord:
        document = await self.conversations.find_one({
            "owner_scope_id": owner_scope_id,
            "conversation_id": conversation_id,
        })
        if document is None:
            raise ChatConversationNotFoundError("Chat conversation not found")
        return ChatConversationRecord.model_validate(document)

    async def list_messages(
        self, owner_scope_id: str, conversation_id: str,
    ) -> list[ChatConversationMessageRecord]:
        await self.get(owner_scope_id, conversation_id)
        cursor = self.messages.find({
            "owner_scope_id": owner_scope_id,
            "conversation_id": conversation_id,
        }).sort("created_at", 1)
        return [ChatConversationMessageRecord.model_validate(item) async for item in cursor]

    async def append_message(
        self,
        owner_scope_id: str,
        conversation_id: str,
        *,
        message_id: str,
        role: ChatMessageRole,
        content: dict[str, Any],
        run_id: str | None,
    ) -> ChatConversationMessageRecord:
        await self.get(owner_scope_id, conversation_id)
        query = {
            "owner_scope_id": owner_scope_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
        }
        existing = await self.messages.find_one(query)
        if existing is not None:
            return ChatConversationMessageRecord.model_validate(existing)
        record = ChatConversationMessageRecord(
            owner_scope_id=owner_scope_id,
            conversation_id=conversation_id,
            message_id=message_id,
            role=role,
            content=content,
            run_id=run_id,
        )
        try:
            await self.messages.insert_one(record.model_dump(mode="python"))
        except DuplicateKeyError:
            existing = await self.messages.find_one(query)
            if existing is None:
                raise
            return ChatConversationMessageRecord.model_validate(existing)
        await self.conversations.update_one(
            {"owner_scope_id": owner_scope_id, "conversation_id": conversation_id},
            {"$set": {"updated_at": record.created_at}},
        )
        return record

    async def replace_message(
        self,
        owner_scope_id: str,
        conversation_id: str,
        message_id: str,
        *,
        role: ChatMessageRole,
        content: dict[str, Any],
        run_id: str | None,
    ) -> ChatConversationMessageRecord:
        await self.get(owner_scope_id, conversation_id)
        query = {
            "owner_scope_id": owner_scope_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
        }
        current = await self.messages.find_one(query)
        if current is None:
            raise ChatConversationNotFoundError("Chat message not found")
        updated_at = datetime.now(timezone.utc)
        await self.messages.update_one(query, {"$set": {
            "role": role,
            "content": content,
            "run_id": run_id,
            "updated_at": updated_at,
        }})
        return ChatConversationMessageRecord.model_validate({
            **current,
            "role": role,
            "content": content,
            "run_id": run_id,
            "updated_at": updated_at,
        })