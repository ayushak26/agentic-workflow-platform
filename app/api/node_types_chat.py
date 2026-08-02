"""Ask AI about node types — explains what a node type does, when to use it,
and its advantages, grounded in the live node registry so it can never
describe a node type that doesn't exist and always reflects whatever node
types are currently registered (no hardcoded description file to go stale).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.nodes.registry import NodeRegistry
from app.security.dependencies import CurrentUser, require_consultant

router = APIRouter(prefix="/api/node-types", tags=["node-types-chat"])

NODE_TYPE_CHAT_MODEL = "gpt-5.6-terra"
PROMPT_DRAFTING_MODEL = "gpt-5.6-luna"

_SYSTEM_PROMPT = (
    "You explain node types available in an agentic workflow builder to a "
    "non-technical audience — someone building a workflow who has never "
    "written code and doesn't know what a 'schema' or 'config field' is. "
    "Use ONLY the NODE TYPE CATALOG below as your source of truth — it is "
    "generated live from this platform's current node registry, so it is "
    "always complete and current — but NEVER surface its technical "
    "vocabulary in your answer. Specifically:\n"
    "- Never say 'config field', 'schema', 'property', 'parameter', "
    "'input_schema'/'output_schema', 'category:' as a label, or list raw "
    "field names verbatim. Translate every one of those into a plain-"
    "English capability instead (e.g. instead of \"has a prompt_template "
    "config field\", say \"you can tell it what to write in your own "
    "words\").\n"
    "- Explain, in simple everyday language: what this node actually does "
    "(as an action a person would recognize, not an implementation "
    "detail), when someone building a workflow should reach for it, and "
    "why it's a good choice compared to similar options — without naming "
    "internal categories or class names as the reason.\n"
    "- Keep answers short, concrete, and example-driven. Prefer 2-4 short "
    "sentences or a short bullet list over a long technical writeup.\n"
    "- If asked about a node type that is NOT in the catalog below, say "
    "plainly that it does not exist in this platform yet — never invent or "
    "assume one exists."
)


def _schema_field_names(schema: dict[str, Any] | None) -> list[str]:
    if not isinstance(schema, dict):
        return []
    return list((schema.get("properties") or {}).keys())


def _build_node_type_catalog() -> str:
    """Always reads NodeRegistry.manifest() fresh — the same live source
    GET /api/node-types serves — so a node type added to the codebase shows
    up here automatically with no other change needed."""
    lines = []
    for entry in NodeRegistry.manifest():
        config_fields = _schema_field_names(entry.get("config_schema"))
        lines.append(
            f"- {entry['type_name']} (category: {entry.get('category', 'Other')}): "
            f"{entry.get('description') or 'no description provided'}. "
            f"Config fields: {', '.join(config_fields) or 'none'}."
        )
    return "\n".join(lines)


def _manifest_entry(type_name: str) -> dict[str, Any] | None:
    """Live lookup — same freshness guarantee as _build_node_type_catalog."""
    for entry in NodeRegistry.manifest():
        if entry["type_name"] == type_name:
            return entry
    return None


class ChatMessage(BaseModel):
    role: str
    content: str


class AskAboutNodeTypesRequest(BaseModel):
    question: str
    focus_type_name: str | None = None
    history: list[ChatMessage] = []


@router.post("/ask")
async def ask_about_node_types(
    req: AskAboutNodeTypesRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    services = request.app.state.services
    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")

    scope = getattr(user, "session_id", None) or user.username
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="node-types-chat", session_id=scope, node_id="node_types_ask",
            ledger=services.get("cost_ledger"),
        )

    catalog = _build_node_type_catalog()
    conversation = "\n".join(f"{m.role}: {m.content}" for m in req.history)
    focus = f"\nThe user is currently focused on: {req.focus_type_name}\n" if req.focus_type_name else ""
    user_prompt = (
        f"NODE TYPE CATALOG:\n{catalog}\n"
        f"{focus}"
        + (f"\nCONVERSATION SO FAR:\n{conversation}\n" if conversation else "")
        + f"\nQUESTION: {req.question}"
    )

    response = await llm.complete(
        model=NODE_TYPE_CHAT_MODEL,
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.2,
    )
    return {"answer": response.text}


_PROMPT_DRAFTING_SYSTEM_PROMPT = (
    "You help workflow authors draft prompt text (a prompt_template, "
    "system_prompt, or instructions config field) for one specific node "
    "type in an agentic workflow builder. You are told the node type, its "
    "live-registry description, and what field is being edited. If the "
    "user's request is clear enough to draft from, respond with ONLY the "
    "drafted prompt text — no commentary, no markdown fences, no quotes "
    "around it, ready to paste directly into the field. If the request is "
    "too vague to draft something useful, ask one concise clarifying "
    "question instead of guessing."
)


class DraftPromptRequest(BaseModel):
    type_name: str
    field_name: str
    instruction: str
    history: list[ChatMessage] = []


@router.post("/draft-prompt")
async def draft_prompt(
    req: DraftPromptRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    services = request.app.state.services
    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")

    entry = _manifest_entry(req.type_name)
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Node type {req.type_name!r} does not exist in this platform's current registry.",
        )

    scope = getattr(user, "session_id", None) or user.username
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="node-types-chat", session_id=scope, node_id="draft_prompt",
            ledger=services.get("cost_ledger"),
        )

    conversation = "\n".join(f"{m.role}: {m.content}" for m in req.history)
    user_prompt = (
        f"Node type: {req.type_name} (category: {entry.get('category', 'Other')})\n"
        f"What this node type does: {entry.get('description') or 'no description provided'}\n"
        f"Field being edited: {req.field_name}\n"
        + (f"\nCONVERSATION SO FAR:\n{conversation}\n" if conversation else "")
        + f"\nUSER REQUEST: {req.instruction}"
    )

    response = await llm.complete(
        model=PROMPT_DRAFTING_MODEL,
        system=_PROMPT_DRAFTING_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.4,
    )
    return {"answer": response.text}
