"""Retrieval Playground search, comparison and trace APIs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.knowledge.repository import ResourceNotFoundError
from app.retrieval.filters import coerce_metadata_filter_group
from app.retrieval.models import RetrievalFilters, RetrievalQuery
from app.retrieval.presets import RETRIEVAL_PRESETS
from app.security.dependencies import CurrentUser, require_permission
from app.security.guardrails import GuardrailViolation, check_workflow_inputs

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


@router.get("/presets")
async def presets(
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    """Return editable starting configurations; saving creates a profile."""

    return RETRIEVAL_PRESETS


def _scope(user: CurrentUser) -> str:
    return user.session_id or user.username


def _service(request: Request):
    service = request.app.state.services.get("retrieval_service")
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Retrieval service unavailable")
    return service


def _llm(request: Request, scope: str, node_id: str, collection_id: str | None = None):
    services = request.app.state.services
    llm = services.get("llm")
    if llm is not None and hasattr(llm, "with_context"):
        return llm.with_context(
            run_id=f"retrieval-playground:{node_id}",
            session_id=scope,
            node_id=node_id,
            ledger=services.get("cost_ledger"),
            collection_id=collection_id or "default",
            workflow_name=f"Retrieval Playground · {node_id}",
        )
    return llm


class RetrievalSearchRequest(BaseModel):
    collection_id: str
    retrieval_profile_id: str | None = None
    retrieval_profile_version: int | None = None
    index_id: str | None = None
    query: str = Field(min_length=1)
    # Accepts either a filter tree ({"logic","predicates","groups"}) or the
    # flat {field: value} shape workflows use. Typed as a plain mapping and
    # coerced explicitly: annotating it as MetadataFilterGroup made a flat
    # dict validate as an *empty* group, so metadata filters were silently
    # dropped and callers got unfiltered results believing they were filtered.
    filters: dict[str, Any] | None = None
    strategy: str = "hybrid"
    candidate_count: int = Field(default=20, ge=1, le=200)
    final_count: int = Field(default=6, ge=1, le=50)
    alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    fusion_strategy: str = "relative_score"
    rerank: bool = True
    compress: bool = True
    query_transform: str = "none"
    context_expansion: str = "none"
    diagnostic_components: bool = True

    def to_query(self, owner_scope_id: str) -> RetrievalQuery:
        return RetrievalQuery(
            query=self.query,
            filters=RetrievalFilters(
                session_id=owner_scope_id,
                collection_id=self.collection_id,
                metadata=coerce_metadata_filter_group(self.filters),
            ),
            retrieval_profile_id=self.retrieval_profile_id,
            retrieval_profile_version=self.retrieval_profile_version,
            index_id=self.index_id,
            strategy=self.strategy,
            top_k_candidates=self.candidate_count,
            top_n_final=self.final_count,
            alpha=self.alpha,
            fusion_strategy=self.fusion_strategy,
            rerank=self.rerank,
            compress=self.compress,
            query_transform=self.query_transform,
            context_expansion=self.context_expansion,
            diagnostic_components=self.diagnostic_components,
        )


def _safe_query(value: str) -> str:
    try:
        return check_workflow_inputs({"query": value}).value["query"]
    except GuardrailViolation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/search")
async def search(
    payload: RetrievalSearchRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("rag:query")),
):
    payload.query = _safe_query(payload.query)
    scope = _scope(user)
    try:
        return await _service(request).retrieve(
            payload.to_query(scope), owner_scope_id=scope,
            llm=_llm(request, scope, "search", payload.collection_id),
        )
    except (ResourceNotFoundError, ValueError, PermissionError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


class RetrievalCompareRequest(BaseModel):
    experiments: list[RetrievalSearchRequest] = Field(min_length=2, max_length=4)


@router.post("/compare")
async def compare(
    payload: RetrievalCompareRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("rag:query")),
):
    scope = _scope(user)
    queries = []
    try:
        for experiment in payload.experiments:
            experiment.query = _safe_query(experiment.query)
            queries.append(experiment.to_query(scope))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    results = await _service(request).compare(
        queries,
        owner_scope_id=scope,
        llm=_llm(request, scope, "compare"),
    )
    overlap: dict[str, list[str]] = {}
    for index, result in enumerate(results):
        overlap[f"experiment_{index + 1}"] = [chunk.chunk_id for chunk in result.chunks]
    pairwise = []
    for left in range(len(results)):
        for right in range(left + 1, len(results)):
            left_ids = set(overlap[f"experiment_{left + 1}"])
            right_ids = set(overlap[f"experiment_{right + 1}"])
            intersection = left_ids & right_ids
            union = left_ids | right_ids
            pairwise.append(
                {
                    "left": left,
                    "right": right,
                    "shared_chunk_ids": sorted(intersection),
                    "shared_count": len(intersection),
                    "jaccard": len(intersection) / len(union) if union else 1.0,
                }
            )
    return {"results": results, "chunk_ids": overlap, "pairwise_overlap": pairwise}


@router.get("/traces")
async def list_traces(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    return await request.app.state.services["knowledge_repository"].list_traces(_scope(user), limit)


@router.get("/traces/{retrieval_request_id}")
async def get_trace(
    retrieval_request_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    try:
        return await request.app.state.services["knowledge_repository"].get_trace(
            _scope(user), retrieval_request_id
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
