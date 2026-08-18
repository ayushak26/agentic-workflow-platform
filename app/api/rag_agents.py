"""Saved RAG Agent management and direct test query API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.knowledge.repository import ResourceNotFoundError
from app.security.dependencies import CurrentUser, require_permission
from app.security.guardrails import GuardrailViolation, check_workflow_inputs

router = APIRouter(prefix="/api/rag-agents", tags=["rag-agents"])


def _scope(user: CurrentUser) -> str:
    return user.session_id or user.username


class RAGAgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    collection_id: str
    retrieval_profile_id: str
    generation_profile_id: str
    routing_profile_id: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: RAGAgentCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("rag:write")),
):
    try:
        return await request.app.state.services["knowledge_service"].create_rag_agent(
            owner_scope_id=_scope(user), **payload.model_dump()
        )
    except (ResourceNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("")
async def list_agents(
    request: Request,
    search: str | None = Query(default=None),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    return await request.app.state.services["knowledge_repository"].list_rag_agents(
        _scope(user), search=search
    )


@router.get("/{rag_agent_id}")
async def get_agent(
    rag_agent_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    try:
        return await request.app.state.services["knowledge_repository"].get_rag_agent(
            _scope(user), rag_agent_id
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


class RAGQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    runtime_filters: dict[str, Any] = Field(default_factory=dict)
    runtime_context: dict[str, Any] = Field(default_factory=dict)


@router.post("/{rag_agent_id}/query")
async def query_agent(
    rag_agent_id: str,
    payload: RAGQueryRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("rag:query")),
):
    try:
        query = check_workflow_inputs({"query": payload.query}).value["query"]
    except GuardrailViolation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    try:
        services = request.app.state.services
        llm = services.get("llm")
        if llm is not None and hasattr(llm, "with_context"):
            llm = llm.with_context(
                run_id=f"rag-agent-test:{rag_agent_id}",
                session_id=_scope(user),
                node_id="rag_agent_query",
                ledger=services.get("cost_ledger"),
            )
        return await request.app.state.services["rag_service"].query(
            owner_scope_id=_scope(user),
            rag_agent_id=rag_agent_id,
            query=query,
            runtime_filters=payload.runtime_filters,
            runtime_context=payload.runtime_context or None,
            llm=llm,
        )
    except (ResourceNotFoundError, PermissionError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
