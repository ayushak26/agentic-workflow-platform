"""Ask AI about a run — a grounded Q&A chat scoped to one run's own data
(status, inputs, per-node outputs/errors). Reuses the same grounded-prompt
pattern as app/api/eval.py's document_qa scoring, and the run data already
assembled for GET /api/runs/mine/{run_id}.
"""
from __future__ import annotations

import json
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.nodes.registry import NodeRegistry
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.run_chat_store import append_run_chat_turn, get_run_chat_turns
from app.workflow.run_history import get_run

router = APIRouter(prefix="/api/runs", tags=["run-chat"])

CHAT_MODEL = "gpt-5.6-luna"

# Fixed, generic starter questions — not LLM-generated, so opening the panel
# costs nothing until the user actually asks something. The third is
# templated with the actual run_id rather than a literal placeholder.
def starter_questions(run_id: str) -> list[str]:
    return [
        "Summary of this task",
        "Which specific step failed or was skipped during this run?",
        f"What was the final status of workflow run ID {run_id}?",
    ]

_SYSTEM_PROMPT = (
    "You are answering questions about one workflow run in an agentic "
    "workflow platform. Answer using ONLY the RUN DATA below — it is the "
    "complete record of this run's status, inputs, per-node outputs, which "
    "node TYPES were used and what each type does, and the prompt(s) each "
    "node was configured with. If the answer isn't in the run data, say so "
    "plainly instead of guessing. Be concise and specific, referencing node "
    "ids where useful."
)

# Keeps the prompt bounded even for a run with many/large node outputs —
# same spirit as run_history.py's _INLINE_VALUE_LIMIT_BYTES, just applied to
# the flattened context string rather than a single Mongo field.
_MAX_CONTEXT_CHARS = 12_000


def _summarize_value(value: Any, limit: int = 800) -> str:
    if value is None:
        return "—"
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


# The fields a node's per-run recorded input redacts before it ever reaches
# run_history (see _REDACTED_KEYS in app/workflow/run_history.py) — prompts
# are treated like credentials there deliberately. We surface the same
# fields here anyway, but sourced from the run's own workflow_yaml (the
# workflow author's own design-time config, unredacted) rather than by
# reading the redacted per-run record — this answers "what prompt was this
# node configured with" without weakening that existing redaction boundary.
_PROMPT_CONFIG_KEYS = ("prompt_template", "system_prompt", "instructions")


def _node_configs_from_workflow_yaml(workflow_yaml: str | None) -> dict[str, dict[str, Any]]:
    if not workflow_yaml:
        return {}
    try:
        parsed = yaml.safe_load(workflow_yaml) or {}
    except yaml.YAMLError:
        return {}
    return {
        node["id"]: node
        for node in (parsed.get("nodes") or [])
        if isinstance(node, dict) and "id" in node
    }


def _node_type_catalog() -> dict[str, dict[str, Any]]:
    return {entry["type_name"]: entry for entry in NodeRegistry.manifest()}


def _build_run_context(run: dict[str, Any]) -> str:
    lines = [
        f"Workflow: {run.get('workflow_name')}",
        f"Status: {run.get('status')}",
        f"Started at: {run.get('started_at')}",
        f"Ended at: {run.get('ended_at')}",
        f"Duration (s): {run.get('duration_s')}",
    ]
    if run.get("error"):
        lines.append(f"Run-level error: {run['error']}")
    if run.get("inputs"):
        lines.append(f"Inputs: {_summarize_value(run['inputs'])}")

    node_configs = _node_configs_from_workflow_yaml(run.get("workflow_yaml"))
    type_catalog = _node_type_catalog()
    node_runs = run.get("node_runs") or {}
    type_names_used = sorted({nr.get("type_name") for nr in node_runs.values() if nr.get("type_name")})
    if type_names_used:
        lines.append(f"Node types used in this run: {', '.join(type_names_used)}")

    lines.append("Nodes:")
    for node_run in node_runs.values():
        node_id = node_run.get("node_id", "?")
        type_name = node_run.get("type_name")
        type_info = type_catalog.get(type_name, {})
        parts = [
            f"- {node_id} ({type_name}): {node_run.get('status')}",
            f"duration={node_run.get('duration_s')}s",
        ]
        if type_info.get("category"):
            parts.append(f"category={type_info['category']}")
        if type_info.get("description"):
            parts.append(f"what this node type does: {type_info['description']}")

        config = node_configs.get(node_id, {}).get("config") or {}
        prompt_fields = {k: v for k, v in config.items() if k in _PROMPT_CONFIG_KEYS and v}
        if prompt_fields:
            parts.append(f"configured prompt(s): {_summarize_value(prompt_fields)}")

        if node_run.get("error"):
            parts.append(f"error={node_run['error']}")
        if node_run.get("output") is not None:
            parts.append(f"output={_summarize_value(node_run['output'])}")
        lines.append(" ".join(parts))

    context = "\n".join(lines)
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS] + "\n… (truncated)"
    return context


class ChatMessage(BaseModel):
    role: str
    content: str


class AskAboutRunRequest(BaseModel):
    question: str
    history: list[ChatMessage] = []


def _scope(user: CurrentUser) -> str:
    return getattr(user, "session_id", None) or user.username


@router.get("/mine/{run_id}/chat")
async def get_run_chat(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run store unavailable")
    scope = _scope(user)
    turns = await get_run_chat_turns(db, session_id=scope, run_id=run_id)
    return {"turns": turns, "starter_questions": starter_questions(run_id)}


@router.post("/mine/{run_id}/chat")
async def ask_about_run(
    run_id: str,
    req: AskAboutRunRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    services = request.app.state.services
    db = services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run store unavailable")
    scope = _scope(user)

    run = await get_run(db, scope, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id=run_id, session_id=scope, node_id="ask_ai",
            ledger=services.get("cost_ledger"),
        )

    context = _build_run_context(run)
    conversation = "\n".join(f"{m.role}: {m.content}" for m in req.history)
    user_prompt = (
        f"RUN DATA:\n{context}\n\n"
        + (f"CONVERSATION SO FAR:\n{conversation}\n\n" if conversation else "")
        + f"QUESTION: {req.question}"
    )

    response = await llm.complete(
        model=CHAT_MODEL,
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.2,
    )

    turns = await append_run_chat_turn(
        db,
        session_id=scope,
        run_id=run_id,
        question=req.question,
        answer=response.text,
        model=response.model,
    )
    return {"turns": turns, "answer": response.text}
