"""Workflow-neutral planning APIs for the Chat workspace."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.chat_workflows import ensure_managed_adapter
from app.security.dependencies import CurrentUser, require_consultant
from app.runtime.loader import load_workflow_from_string
from app.workflow.chat_workspace_planner import (
    CHAT_ANSWER_MODEL,
    EXPERIENCES,
    build_existing_workflow_chat_adapter,
    plan_workspace,
)


router = APIRouter(prefix="/api/chat-workspace", tags=["chat-workspace"])
_WORKFLOW_LABEL_CACHE: dict[str, str] = {}
_GENERIC_WORKFLOW_WORDS = {
    "a", "agent", "an", "and", "assistant", "automation", "database", "demo",
    "for", "flow", "generic", "of", "pipeline", "process", "sample", "test",
    "the", "to", "tool", "with", "workflow",
}


def _managed_adapter_key(body: PlanWorkspaceRequest, plan) -> str:
    """Stable owner-local identity for execution-equivalent Chat adapters."""
    if plan.existing_workflow:
        return f"existing:{plan.existing_workflow}"
    if body.previous_run_id:
        return f"previous-result:{body.previous_run_id}:{plan.kind}:{body.preferred_output}"
    if body.rag_agent_id:
        document_scope = hashlib.sha256(
            json.dumps(sorted(set(body.document_ids))).encode("utf-8")
        ).hexdigest()[:12] if body.document_ids else "all"
        return f"rag:{body.rag_agent_id}:documents:{document_scope}"
    if body.skill_name:
        return f"skill:{body.skill_name}"
    if body.integration_connection and body.integration_tool:
        return f"integration:{body.integration_connection}:{body.integration_tool}"
    if body.collection_id or body.retrieval_profile_id:
        return f"retrieval:{body.collection_id or ''}:{body.retrieval_profile_id or ''}"
    categories = ",".join(sorted(set(body.attachment_categories)))
    capabilities = ",".join(sorted(set(plan.capabilities)))
    experience = body.experience_id or "default"
    return ":".join((
        plan.kind,
        experience,
        body.preferred_output,
        "attachments" if body.has_attachments else "no-attachments",
        categories or "none",
        capabilities or "none",
    ))


class PlanWorkspaceRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=20_000)
    experience_id: str | None = None
    skill_name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    selected_workflow: str | None = None
    preferred_output: Literal["auto", "text", "image", "pdf", "docx", "pptx"] = "auto"
    has_attachments: bool = False
    attachment_categories: list[str] = Field(default_factory=list)
    collection_id: str | None = None
    retrieval_profile_id: str | None = None
    rag_agent_id: str | None = None
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    integration_connection: str | None = None
    integration_tool: str | None = None
    previous_run_id: str | None = None


class WorkflowLabelInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    title: str = Field(default="", max_length=240)
    description: str = Field(default="", max_length=4_000)
    use_case: str = Field(default="generic", max_length=240)


class WorkflowLabelsRequest(BaseModel):
    workflows: list[WorkflowLabelInput] = Field(min_length=1, max_length=200)


class GeneratedWorkflowLabel(BaseModel):
    name: str
    label: str


class GeneratedWorkflowLabels(BaseModel):
    labels: list[GeneratedWorkflowLabel]


def _humanize_workflow_name(value: str) -> str:
    value = re.sub(r"^(?:w\d+|sp\d+|w\d+sub)[_-]*", "", value, flags=re.I)
    words = re.sub(r"[_-]+", " ", value).split()
    return " ".join(word.upper() if len(word) <= 3 and word.isalpha() else word.capitalize() for word in words) or "Workflow"


def _weak_workflow_label(item: WorkflowLabelInput) -> bool:
    title = item.title.strip()
    if not title:
        return True
    words = re.findall(r"[A-Za-z0-9]+", title.lower())
    if len(words) <= 1:
        return True
    informative = [word for word in words if word not in _GENERIC_WORKFLOW_WORDS and not re.fullmatch(r"v?\d+", word)]
    return len(informative) <= 1


def _workflow_label_fingerprint(item: WorkflowLabelInput) -> str:
    payload = item.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _clean_workflow_label(value: str, fallback: str) -> str:
    label = re.sub(r"\s+", " ", value).strip().strip("\"'`.-:;")
    words = re.findall(r"[A-Za-z0-9]+", label.lower())
    if (
        not label
        or len(label) > 72
        or not 2 <= len(words) <= 7
        or any(word in {"agent", "pipeline", "workflow"} for word in words)
    ):
        return fallback
    return label


@router.get("/experiences")
async def list_workspace_experiences(
    user: CurrentUser = Depends(require_consultant),
):
    del user
    return {"experiences": [item.public() for item in EXPERIENCES]}


@router.post("/workflow-labels")
async def chat_workflow_labels(
    body: WorkflowLabelsRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Return Chat-only business labels without renaming saved workflows."""
    labels: dict[str, str] = {}
    unresolved: list[WorkflowLabelInput] = []
    fingerprints: dict[str, str] = {}
    for item in body.workflows:
        fallback = item.title.strip() or _humanize_workflow_name(item.name)
        if not _weak_workflow_label(item):
            labels[item.name] = fallback
            continue
        fingerprint = _workflow_label_fingerprint(item)
        fingerprints[item.name] = fingerprint
        cached = _WORKFLOW_LABEL_CACHE.get(fingerprint)
        if cached:
            labels[item.name] = cached
        else:
            unresolved.append(item)

    if unresolved:
        services = getattr(request.app.state, "services", {})
        llm = services.get("llm")
        if llm is not None:
            scope = getattr(user, "session_id", None) or user.username
            if hasattr(llm, "with_context"):
                llm = llm.with_context(
                    run_id="chat:workflow-labels",
                    session_id=scope,
                    node_id="workflow_labels",
                    ledger=services.get("cost_ledger"),
                    workflow_name="Chat workflow naming",
                )
            prompt_items = [item.model_dump(mode="json") for item in unresolved]
            try:
                generated = await llm.complete_structured(
                    model=CHAT_ANSWER_MODEL,
                    system=(
                        "Create concise business-friendly menu labels for workflows. Use 2-7 words. "
                        "Describe the business outcome, not implementation. Do not include ids, version "
                        "numbers, 'workflow', 'agent', 'pipeline', or unexplained acronyms. Return exactly "
                        "one label for every supplied name and preserve each name unchanged."
                    ),
                    user=json.dumps(prompt_items, ensure_ascii=False),
                    response_model=GeneratedWorkflowLabels,
                    temperature=0.1,
                    max_tokens=2_000,
                )
                generated_by_name = {item.name: item.label for item in generated.labels}
                for item in unresolved:
                    fallback = _humanize_workflow_name(item.name)
                    label = _clean_workflow_label(generated_by_name.get(item.name, ""), fallback)
                    labels[item.name] = label
                    _WORKFLOW_LABEL_CACHE[fingerprints[item.name]] = label
            except Exception:
                # Naming is a convenience; Chat must remain usable when the
                # provider is unavailable or returns an invalid batch.
                pass

        for item in unresolved:
            labels.setdefault(item.name, _humanize_workflow_name(item.name))

    return {"labels": labels}


@router.post("/plan")
async def plan_chat_workspace(
    body: PlanWorkspaceRequest,
    user: CurrentUser = Depends(require_consultant),
):
    del user
    return plan_workspace(**body.model_dump()).public()


@router.post("/prepare", status_code=status.HTTP_201_CREATED)
async def prepare_chat_workspace(
    body: PlanWorkspaceRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    if body.skill_name:
        catalog = getattr(request.app.state, "services", {}).get("scientific_skill_catalog")
        if catalog is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Scientific Agent Skills are disabled")
        if body.skill_name not in catalog.loaded_skill_names:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Selected Scientific Agent Skill is not approved or unavailable")
    previous_result = None
    if body.previous_run_id:
        db = getattr(request.app.state, "services", {}).get("audit_db")
        if db is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Run history is unavailable")
        from app.workflow.run_history import get_run

        previous = await get_run(db, getattr(user, "session_id", None) or user.username, body.previous_run_id)
        if previous is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Previous workflow run not found")
        previous_result = {
            "run_id": body.previous_run_id,
            "workflow_name": previous.get("workflow_name"),
            "outputs": previous.get("outputs"),
        }
    plan = plan_workspace(**body.model_dump(), previous_result=previous_result)
    if plan.missing_requirements:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": plan.reason, "missing_requirements": list(plan.missing_requirements), "plan": plan.public()},
        )
    if plan.existing_workflow:
        from app.api.chat_workflows import WORKFLOWS_DIR

        path = WORKFLOWS_DIR / f"{plan.existing_workflow}.yaml"
        if not path.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Selected workflow not found")
        selected_yaml = path.read_text(encoding="utf-8")
        try:
            selected_spec = load_workflow_from_string(selected_yaml)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Selected workflow is not valid: {exc}",
            ) from exc
        yaml_text = build_existing_workflow_chat_adapter(
            plan.existing_workflow,
            selected_spec,
        )
        source = "existing"
        source_workflow_name = plan.existing_workflow
    else:
        if not plan.yaml_text:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No executable workflow could be prepared")
        yaml_text = plan.yaml_text
        source = "imported"
        source_workflow_name = None
    record = await ensure_managed_adapter(
        request, user,
        adapter_key=_managed_adapter_key(body, plan), display_name=plan.title,
        yaml_text=yaml_text, source=source,
        source_workflow_name=source_workflow_name,
    )
    return {"plan": plan.public(), "workflow": record.public_summary()}