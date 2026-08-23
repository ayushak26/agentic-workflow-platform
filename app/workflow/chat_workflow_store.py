"""Owner-scoped persistence for private Business Chat workflows."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError


CHAT_WORKFLOWS_COLLECTION = "chat_workflows"


def _default_output_compatibility() -> dict[str, Any]:
    """Safe response shape for records created before output analysis existed."""
    return {
        "supported": True,
        "detected_types": ["text"],
        "fallback_to_text": True,
        "warnings": [
            "Output compatibility was not recorded when this private workflow was created.",
        ],
    }


class ChatWorkflowConflictError(ValueError):
    """A private workflow slug already exists for this owner."""


class ChatWorkflowNotFoundError(LookupError):
    """A private workflow was not found in the authenticated owner scope."""


class ChatWorkflowRecord(BaseModel):
    chat_workflow_id: str
    owner_scope_id: str
    slug: str
    display_name: str
    description: str = ""
    yaml: str
    source: Literal["generated", "imported", "existing"]
    status: Literal["private", "publish_requested", "published", "archived"] = "private"
    source_workflow_name: str | None = None
    output_compatibility: dict[str, Any] = Field(
        default_factory=_default_output_compatibility,
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = 1

    def public_summary(self) -> dict[str, Any]:
        """Return card metadata without owner identity or workflow YAML."""
        return {
            "id": self.chat_workflow_id,
            "slug": self.slug,
            "name": self.display_name,
            "description": self.description,
            "source": self.source,
            "visibility": "private",
            "status": self.status,
            "source_workflow_name": self.source_workflow_name,
            "output_compatibility": self.output_compatibility,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


async def ensure_chat_workflow_indexes(db: Any) -> None:
    collection = db[CHAT_WORKFLOWS_COLLECTION]
    await collection.create_index("chat_workflow_id", unique=True)
    await collection.create_index(
        [("owner_scope_id", 1), ("slug", 1)], unique=True,
    )
    await collection.create_index(
        [("owner_scope_id", 1), ("status", 1), ("updated_at", -1)],
    )


class ChatWorkflowStore:
    """CRUD where every query is pinned to an authenticated owner scope."""

    def __init__(self, db: Any):
        self.collection = db[CHAT_WORKFLOWS_COLLECTION]

    async def create(
        self,
        *,
        owner_scope_id: str,
        slug: str,
        display_name: str,
        description: str,
        yaml_text: str,
        source: Literal["generated", "imported", "existing"],
        source_workflow_name: str | None = None,
        output_compatibility: dict[str, Any] | None = None,
    ) -> ChatWorkflowRecord:
        existing = await self.collection.find_one({
            "owner_scope_id": owner_scope_id,
            "slug": slug,
        })
        if existing is not None:
            raise ChatWorkflowConflictError(
                f"A private Chat workflow named '{slug}' already exists",
            )
        record = ChatWorkflowRecord(
            chat_workflow_id=f"cwf_{uuid.uuid4().hex}",
            owner_scope_id=owner_scope_id,
            slug=slug,
            display_name=display_name,
            description=description,
            yaml=yaml_text,
            source=source,
            source_workflow_name=source_workflow_name,
            output_compatibility=(
                output_compatibility
                if output_compatibility is not None
                else _default_output_compatibility()
            ),
        )
        try:
            await self.collection.insert_one(record.model_dump(mode="python"))
        except DuplicateKeyError as exc:
            raise ChatWorkflowConflictError(
                f"A private Chat workflow named '{slug}' already exists",
            ) from exc
        return record

    async def list_private(self, owner_scope_id: str) -> list[ChatWorkflowRecord]:
        cursor = self.collection.find({
            "owner_scope_id": owner_scope_id,
            "status": {"$nin": ["archived"]},
        }).sort("updated_at", -1)
        return [ChatWorkflowRecord.model_validate(document) async for document in cursor]

    async def get(self, owner_scope_id: str, chat_workflow_id: str) -> ChatWorkflowRecord:
        document = await self.collection.find_one({
            "owner_scope_id": owner_scope_id,
            "chat_workflow_id": chat_workflow_id,
            "status": {"$nin": ["archived"]},
        })
        if document is None:
            raise ChatWorkflowNotFoundError("Private Chat workflow not found")
        return ChatWorkflowRecord.model_validate(document)

    async def get_by_slug(self, owner_scope_id: str, slug: str) -> ChatWorkflowRecord | None:
        document = await self.collection.find_one({
            "owner_scope_id": owner_scope_id,
            "slug": slug,
            "status": {"$nin": ["archived"]},
        })
        return ChatWorkflowRecord.model_validate(document) if document else None

    async def archive(self, owner_scope_id: str, chat_workflow_id: str) -> bool:
        try:
            await self.get(owner_scope_id, chat_workflow_id)
        except ChatWorkflowNotFoundError:
            return False
        await self.collection.update_one(
            {
                "owner_scope_id": owner_scope_id,
                "chat_workflow_id": chat_workflow_id,
                "status": {"$nin": ["archived"]},
            },
            {"$set": {
                "status": "archived",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return True

    async def request_publication(
        self, owner_scope_id: str, chat_workflow_id: str,
    ) -> ChatWorkflowRecord:
        record = await self.get(owner_scope_id, chat_workflow_id)
        await self.collection.update_one(
            {
                "owner_scope_id": owner_scope_id,
                "chat_workflow_id": chat_workflow_id,
            },
            {"$set": {
                "status": "publish_requested",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return record.model_copy(update={
            "status": "publish_requested",
            "updated_at": datetime.now(timezone.utc),
        })