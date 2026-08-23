"""Workflow-neutral planning APIs for the Chat workspace."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.chat_workflows import _save
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.chat_workspace_planner import EXPERIENCES, plan_workspace


router = APIRouter(prefix="/api/chat-workspace", tags=["chat-workspace"])


class PlanWorkspaceRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=20_000)
    experience_id: str | None = None
    selected_workflow: str | None = None
    preferred_output: Literal["auto", "text", "pdf", "pptx"] = "auto"
    has_attachments: bool = False
    attachment_categories: list[str] = Field(default_factory=list)
    collection_id: str | None = None
    retrieval_profile_id: str | None = None
    rag_agent_id: str | None = None
    integration_connection: str | None = None
    integration_tool: str | None = None
    previous_run_id: str | None = None


@router.get("/experiences")
async def list_workspace_experiences(
    user: CurrentUser = Depends(require_consultant),
):
    del user
    return {"experiences": [item.public() for item in EXPERIENCES]}


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
        yaml_text = path.read_text(encoding="utf-8")
        source = "existing"
        source_workflow_name = plan.existing_workflow
    else:
        if not plan.yaml_text:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No executable workflow could be prepared")
        yaml_text = plan.yaml_text
        source = "imported"
        source_workflow_name = None
    suffix = uuid.uuid4().hex[:10]
    record = await _save(
        request, user,
        slug=f"workspace-{suffix}", display_name=plan.title,
        yaml_text=yaml_text, source=source,
        source_workflow_name=source_workflow_name,
    )
    return {"plan": plan.public(), "workflow": record.public_summary()}