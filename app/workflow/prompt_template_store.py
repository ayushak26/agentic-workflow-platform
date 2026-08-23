"""Owner-scoped custom prompt templates plus immutable built-in templates."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field


PROMPT_TEMPLATES_COLLECTION = "prompt_templates"
PromptCategory = Literal[
    "Research", "Summarize", "Compare", "Extract", "Brainstorm", "Writing", "Analysis",
]
_VARIABLE = re.compile(r"{{\s*([A-Za-z][A-Za-z0-9_]*)\s*}}")


def template_variables(content: str) -> list[str]:
    """Return unique placeholders in first-appearance order."""
    return list(dict.fromkeys(_VARIABLE.findall(content)))


class PromptTemplateRecord(BaseModel):
    template_id: str
    owner_scope_id: str
    title: str
    description: str = ""
    category: PromptCategory
    content: str
    variables: list[str] = Field(default_factory=list)
    favorite: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def public(self) -> dict[str, Any]:
        return {
            "id": self.template_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "content": self.content,
            "variables": self.variables,
            "favorite": self.favorite,
            "built_in": False,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


BUILT_IN_TEMPLATES: tuple[dict[str, Any], ...] = (
    {"id": "builtin_research_brief", "title": "Research brief", "description": "Research a topic with scope and evidence requirements.", "category": "Research", "content": "Research {{topic}} for {{audience}}. Focus on {{scope}} and provide a {{length}} evidence-grounded brief."},
    {"id": "builtin_summarize", "title": "Focused summary", "description": "Summarize material for a defined audience.", "category": "Summarize", "content": "Summarize {{topic}} for {{audience}} in a {{tone}} tone. Keep it to {{length}}."},
    {"id": "builtin_compare", "title": "Structured comparison", "description": "Compare alternatives across explicit criteria.", "category": "Compare", "content": "Compare {{option_a}} and {{option_b}} across {{criteria}}. Conclude with a recommendation for {{audience}}."},
    {"id": "builtin_extract", "title": "Extract key fields", "description": "Extract a consistent set of fields.", "category": "Extract", "content": "Extract {{fields}} from {{source}}. Mark missing values explicitly and preserve exact wording where relevant."},
    {"id": "builtin_brainstorm", "title": "Idea generator", "description": "Generate and rank practical ideas.", "category": "Brainstorm", "content": "Brainstorm {{count}} ideas for {{topic}} under these constraints: {{constraints}}. Rank them by {{criteria}}."},
    {"id": "builtin_writing", "title": "Draft for an audience", "description": "Create a polished draft with controlled tone.", "category": "Writing", "content": "Write a {{length}} {{document_type}} about {{topic}} for {{audience}} in a {{tone}} tone."},
    {"id": "builtin_analysis", "title": "Decision analysis", "description": "Analyze evidence, risks, and implications.", "category": "Analysis", "content": "Analyze {{topic}} using {{framework}}. Identify evidence, assumptions, risks, and recommended next actions."},
)


def built_in_templates() -> list[dict[str, Any]]:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{
        **item,
        "variables": template_variables(item["content"]),
        "favorite": False,
        "built_in": True,
        "created_at": now,
        "updated_at": now,
    } for item in BUILT_IN_TEMPLATES]


async def ensure_prompt_template_indexes(db: Any) -> None:
    collection = db[PROMPT_TEMPLATES_COLLECTION]
    await collection.create_index("template_id", unique=True)
    await collection.create_index([("owner_scope_id", 1), ("updated_at", -1)])


class PromptTemplateStore:
    def __init__(self, db: Any):
        self.collection = db[PROMPT_TEMPLATES_COLLECTION]

    async def create(self, owner_scope_id: str, *, title: str, description: str, category: PromptCategory, content: str) -> PromptTemplateRecord:
        record = PromptTemplateRecord(
            template_id=f"pt_{uuid.uuid4().hex}", owner_scope_id=owner_scope_id,
            title=title.strip(), description=description.strip(), category=category,
            content=content, variables=template_variables(content),
        )
        await self.collection.insert_one(record.model_dump(mode="python"))
        return record

    async def list(self, owner_scope_id: str) -> list[PromptTemplateRecord]:
        cursor = self.collection.find({"owner_scope_id": owner_scope_id}).sort("updated_at", -1)
        return [PromptTemplateRecord.model_validate(doc) async for doc in cursor]

    async def get(self, owner_scope_id: str, template_id: str) -> PromptTemplateRecord | None:
        doc = await self.collection.find_one({"owner_scope_id": owner_scope_id, "template_id": template_id})
        return PromptTemplateRecord.model_validate(doc) if doc else None

    async def update(self, owner_scope_id: str, template_id: str, **changes: Any) -> PromptTemplateRecord | None:
        current = await self.get(owner_scope_id, template_id)
        if current is None:
            return None
        payload = {**changes, "updated_at": datetime.now(timezone.utc)}
        if "content" in changes:
            payload["variables"] = template_variables(changes["content"])
        await self.collection.update_one(
            {"owner_scope_id": owner_scope_id, "template_id": template_id},
            {"$set": payload},
        )
        return current.model_copy(update=payload)

    async def delete(self, owner_scope_id: str, template_id: str) -> bool:
        result = await self.collection.delete_one({"owner_scope_id": owner_scope_id, "template_id": template_id})
        return bool(result.deleted_count)