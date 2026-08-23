"""EU Proposal Evidence and Reasoning System API."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.nodes.claim_evidence_verifier import ClaimEvidenceVerifier
from app.nodes.horizon_docx_renderer import HorizonDOCXProposalRenderer
from app.nodes.html_proposal_renderer import HorizonHTMLProposalRenderer
from app.nodes.proposal_evidence_factory import ProposalEvidenceFactoryAgent
from app.evidence.models import (
    CandidateSource,
    EvidencePolicy,
    FullTextDocument,
    RejectedCandidate,
    SearchAuditRecord,
)
from app.proposal_graph import PROPOSAL_NAMESPACE
from app.proposal_graph.concepts import generate_concept_alternatives
from app.proposal_graph.coverage import build_call_coverage_matrix
from app.proposal_graph.graph import ProposalGraph, merge_graph
from app.proposal_graph.horizon_evaluator import evaluate_horizon_proposal
from app.proposal_graph.models import Authority
from app.proposal_graph.state import proposal_graph_state_update
from app.proposal_graph.workspace_store import ProposalWorkspaceStore
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.run_history import get_retry_checkpoint, get_run

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def _scope(user: CurrentUser) -> str:
    """Internal helper for the scope step.

    Args:
        user (CurrentUser): Authenticated current user.

    Returns:
        str: The result.
    """
    return getattr(user, "session_id", None) or user.username


def _services(request: Request) -> dict[str, Any]:
    """Internal helper for the services step.

    Args:
        request (Request): Incoming FastAPI request.

    Returns:
        dict[str, Any]: The result.
    """
    return getattr(request.app.state, "services", {})


def _store(request: Request, *, require_objects: bool = False):
    """Internal helper for the store step.

    Args:
        request (Request): Incoming FastAPI request.
        require_objects (bool): The require objects (optional, default False).
    """
    services = _services(request)
    db = services.get("audit_db")
    object_store = services.get("object_store")
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "proposal store unavailable",
        )
    if require_objects and object_store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "object store unavailable",
        )
    return ProposalWorkspaceStore(db, object_store)


def _graph_from_checkpoint(checkpoint: dict[str, Any]) -> ProposalGraph:
    """Internal helper for the graph from checkpoint step.

    Args:
        checkpoint (dict[str, Any]): Checkpoint document.

    Returns:
        ProposalGraph: The from checkpoint.
    """
    graph: dict[str, Any] = ProposalGraph().model_dump()
    for node_result in (checkpoint.get("node_results") or {}).values():
        extra_state = node_result.get("extra_state") or {}
        proposal_delta = (
            (extra_state.get("domain_state") or {}).get(PROPOSAL_NAMESPACE)
            or {}
        )
        if proposal_delta:
            graph = merge_graph(graph, proposal_delta)
    return ProposalGraph(**graph)


class GraphRequest(BaseModel):
    """Pydantic model defining the GraphRequest shape.

    Attributes:
        graph (ProposalGraph).
    """
    graph: ProposalGraph


@router.post("/coverage")
async def coverage(
    body: GraphRequest,
    user: CurrentUser = Depends(require_consultant),
):
    """Compute the coverage.

    Args:
        body (GraphRequest): Request body.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    del user
    return build_call_coverage_matrix(body.graph).model_dump(mode="json")


@router.get("/runs/{run_id}/review")
async def review_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Compute the review run.

    Args:
        run_id (str): Workflow run identifier.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = _services(request)
    db = services.get("audit_db")
    if db is None:
        raise HTTPException(503, "proposal store unavailable")
    scope = _scope(user)
    run = await get_run(db, scope, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    checkpoint = await get_retry_checkpoint(db, scope, run_id)
    if checkpoint is None:
        raise HTTPException(409, "run has no proposal checkpoint")
    graph = _graph_from_checkpoint(checkpoint)
    store = ProposalWorkspaceStore(db, services.get("object_store"))
    return {
        "proposal_id": run_id,
        "run_status": run.get("status"),
        "graph": graph.model_dump(mode="json"),
        "coverage": build_call_coverage_matrix(graph).model_dump(mode="json"),
        "approvals": await store.list_approvals(
            session_id=scope,
            proposal_id=run_id,
        ),
        "source_versions": await store.list_source_versions(
            session_id=scope,
            proposal_id=run_id,
        ),
    }


class SourceVersionRequest(BaseModel):
    """Pydantic model defining the SourceVersionRequest shape.

    Attributes:
        content (str).
        title (str).
        identifier (str | None).
        authority (Authority).
        metadata (dict[str, Any]).
    """
    content: str
    title: str
    identifier: str | None = None
    authority: Authority = Authority.UNVERIFIED
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/{proposal_id}/sources/{source_id}/versions")
async def register_source_version(
    proposal_id: str,
    source_id: str,
    body: SourceVersionRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Register the source version.

    Args:
        proposal_id (str): The proposal id.
        source_id (str): The source id.
        body (SourceVersionRequest): Request body.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    try:
        record = await _store(
            request,
            require_objects=True,
        ).register_source_version(
            session_id=_scope(user),
            proposal_id=proposal_id,
            source_id=source_id,
            content=body.content,
            title=body.title,
            identifier=body.identifier,
            authority=body.authority.value,
            metadata=body.metadata,
            created_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return record.model_dump(mode="json")


@router.get("/{proposal_id}/sources/versions")
async def list_source_versions(
    proposal_id: str,
    request: Request,
    source_id: str | None = None,
    user: CurrentUser = Depends(require_consultant),
):
    """List the source versions.

    Args:
        proposal_id (str): The proposal id.
        request (Request): Incoming FastAPI request.
        source_id (str | None): The source id (optional, default None).
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    versions = await _store(request).list_source_versions(
        session_id=_scope(user),
        proposal_id=proposal_id,
        source_id=source_id,
    )
    return {"versions": versions}


class VerifyClaimsRequest(BaseModel):
    """Pydantic model defining the VerifyClaimsRequest shape.

    Attributes:
        graph (ProposalGraph).
        model (str).
        minimum_support_confidence (float).
    """
    graph: ProposalGraph
    model: str = "claude-sonnet-4-5"
    minimum_support_confidence: float = 0.72


@router.post("/{proposal_id}/verify-claims")
async def verify_claims(
    proposal_id: str,
    body: VerifyClaimsRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Verify the claims.

    Args:
        proposal_id (str): The proposal id.
        body (VerifyClaimsRequest): Request body.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = _services(request)
    store = _store(request, require_objects=True)
    graph = body.graph.model_copy(deep=True)

    # Pin every known source to its latest immutable version before judging.
    for source_id, source in list(graph.evidence_sources.items()):
        try:
            version, _ = await store.source_text(
                session_id=_scope(user),
                proposal_id=proposal_id,
                source_id=source_id,
            )
        except KeyError:
            continue
        graph.evidence_sources[source_id] = source.model_copy(
            update={
                "version_id": version.version_id,
                "content_sha256": version.content_sha256,
                "object_key": version.object_key,
                "title": version.title,
                "identifier": version.identifier or source.identifier,
                "authority": Authority(version.authority),
            }
        )

    node = ClaimEvidenceVerifier(
        "verify_claims",
        {
            "model": body.model,
            "minimum_support_confidence": body.minimum_support_confidence,
        },
        services=services,
    )
    result = await node.run(
        proposal_graph_state_update(graph),
        node.config.model_dump(),
    )
    delta = (
        (result.pop("__state__").get("domain_state") or {})
        .get(PROPOSAL_NAMESPACE)
        or {}
    )
    updated_graph = ProposalGraph(**merge_graph(graph, delta))
    return {
        **result,
        "graph": updated_graph.model_dump(mode="json"),
        "coverage": build_call_coverage_matrix(updated_graph).model_dump(
            mode="json"
        ),
    }


class ConceptRequest(BaseModel):
    """Pydantic model defining the ConceptRequest shape.

    Attributes:
        graph (ProposalGraph).
        concept_note (str).
        model (str).
    """
    graph: ProposalGraph
    concept_note: str = ""
    model: str = "claude-opus-5"


@router.post("/{proposal_id}/concept-alternatives")
async def concept_alternatives(
    proposal_id: str,
    body: ConceptRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Compute the concept alternatives.

    Args:
        proposal_id (str): The proposal id.
        body (ConceptRequest): Request body.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = _services(request)
    result = await generate_concept_alternatives(
        services["llm"],
        graph=body.graph,
        model=body.model,
        concept_note=body.concept_note,
        skill_catalog=services.get("scientific_skill_catalog"),
    )
    graph = body.graph.model_copy(deep=True)
    graph.concept_alternatives = {
        item.id: item for item in result.alternatives
    }
    snapshot = await _store(request).save_snapshot(
        session_id=_scope(user),
        proposal_id=proposal_id,
        graph=graph,
        created_by=user.username,
        reason="concept_alternatives_generated",
    )
    return {
        **result.model_dump(mode="json"),
        "graph": graph.model_dump(mode="json"),
        "snapshot": snapshot.model_dump(mode="json"),
    }


class ApprovalRequest(BaseModel):
    """Pydantic model defining the ApprovalRequest shape.

    Attributes:
        graph (ProposalGraph).
        stage (str).
        selected_concept_id (str | None).
    """
    graph: ProposalGraph
    stage: str
    selected_concept_id: str | None = None


@router.post("/{proposal_id}/approvals")
async def request_approval(
    proposal_id: str,
    body: ApprovalRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Compute the request approval.

    Args:
        proposal_id (str): The proposal id.
        body (ApprovalRequest): Request body.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    try:
        approval = await _store(request).request_approval(
            session_id=_scope(user),
            proposal_id=proposal_id,
            graph=body.graph,
            stage=body.stage,
            requested_by=user.username,
            selected_concept_id=body.selected_concept_id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return approval.model_dump(mode="json")


class ApprovalDecisionRequest(BaseModel):
    """Pydantic model defining the ApprovalDecisionRequest shape.

    Attributes:
        decision (Literal['approved', 'rejected', 'changes_requested']).
        comment (str | None).
    """
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str | None = None


@router.post("/{proposal_id}/approvals/{approval_id}/decision")
async def decide_approval(
    proposal_id: str,
    approval_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Compute the decide approval.

    Args:
        proposal_id (str): The proposal id.
        approval_id (str): The approval id.
        body (ApprovalDecisionRequest): Request body.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    try:
        approval = await _store(request).decide_approval(
            session_id=_scope(user),
            proposal_id=proposal_id,
            approval_id=approval_id,
            decision=body.decision,
            decided_by=user.username,
            comment=body.comment,
        )
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc
    return approval.model_dump(mode="json")


@router.get("/{proposal_id}/approvals")
async def list_approvals(
    proposal_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """List the approvals.

    Args:
        proposal_id (str): The proposal id.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    approvals = await _store(request).list_approvals(
        session_id=_scope(user),
        proposal_id=proposal_id,
    )
    return {"approvals": approvals}


class HorizonEvaluationRequest(BaseModel):
    """Pydantic model defining the HorizonEvaluationRequest shape.

    Attributes:
        graph (ProposalGraph).
        proposal_text (str).
        generator_model (str | None).
        evaluator_models (list[str]).
        criterion_threshold (float).
        total_threshold (float).
    """
    graph: ProposalGraph
    proposal_text: str
    generator_model: str | None = None
    evaluator_models: list[str] = Field(
        default_factory=lambda: ["claude-sonnet-4-5", "gpt-5"]
    )
    criterion_threshold: float = 3.0
    total_threshold: float = 10.0


@router.post("/{proposal_id}/horizon-evaluation")
async def horizon_evaluation(
    proposal_id: str,
    body: HorizonEvaluationRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Compute the horizon evaluation.

    Args:
        proposal_id (str): The proposal id.
        body (HorizonEvaluationRequest): Request body.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = _services(request)
    try:
        report = await evaluate_horizon_proposal(
            services["llm"],
            proposal_text=body.proposal_text,
            graph=body.graph,
            generator_model=body.generator_model,
            evaluator_models=body.evaluator_models,
            criterion_threshold=body.criterion_threshold,
            total_threshold=body.total_threshold,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db = services.get("audit_db")
    if db is not None:
        await db["horizon_evaluations"].insert_one(
            {
                "session_id": _scope(user),
                "proposal_id": proposal_id,
                "created_at": datetime.now(timezone.utc),
                **report.model_dump(),
            }
        )
    return report.model_dump(mode="json")


class ProposalEvidenceFactoryRequest(BaseModel):
    """Pydantic model defining the ProposalEvidenceFactoryRequest shape.

    Attributes:
        graph (ProposalGraph).
        candidates (list[CandidateSource]).
        documents (list[FullTextDocument]).
        search_audit (list[SearchAuditRecord]).
        rejected_candidates (list[RejectedCandidate]).
        policy (EvidencePolicy).
        model (str).
    """
    graph: ProposalGraph
    candidates: list[CandidateSource]
    documents: list[FullTextDocument]
    search_audit: list[SearchAuditRecord] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    model: str = "claude-sonnet-4-5"


@router.post("/{proposal_id}/evidence/verify")
async def run_proposal_evidence_factory(
    proposal_id: str,
    body: ProposalEvidenceFactoryRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Verify already-fetched full text and return a citable evidence package."""

    services = _services(request)
    if services.get("llm") is None or services.get("object_store") is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "evidence verification services unavailable",
        )
    node = ProposalEvidenceFactoryAgent(
        "proposal_evidence_factory",
        {
            "candidates": [
                item.model_dump(mode="json") for item in body.candidates
            ],
            "documents": [
                item.model_dump(mode="json") for item in body.documents
            ],
            "search_audit": [
                item.model_dump(mode="json") for item in body.search_audit
            ],
            "rejected_candidates": [
                item.model_dump(mode="json")
                for item in body.rejected_candidates
            ],
            "policy": body.policy.model_dump(mode="json"),
            "model": body.model,
        },
        services=services,
    )
    result = await node.run(
        proposal_graph_state_update(body.graph),
        node.config.model_dump(),
    )
    delta = (
        (result.pop("__state__").get("domain_state") or {})
        .get(PROPOSAL_NAMESPACE)
        or {}
    )
    result["graph"] = ProposalGraph(
        **merge_graph(body.graph, delta)
    ).model_dump(mode="json")
    return result


class RenderProposalRequest(BaseModel):
    """Pydantic model defining the RenderProposalRequest shape.

    Attributes:
        content (str).
        content_format (Literal['markdown', 'html']).
        metadata (dict[str, Any]).
        citation_registry (list[dict[str, Any]]).
        evidence_qa (dict[str, Any]).
        evidence_blockers (list[str]).
        include_toc (bool).
        include_bibliography (bool).
    """
    content: str
    content_format: Literal["markdown", "html"] = "markdown"
    metadata: dict[str, Any] = Field(default_factory=dict)
    citation_registry: list[dict[str, Any]] = Field(default_factory=list)
    evidence_qa: dict[str, Any] = Field(default_factory=dict)
    evidence_blockers: list[str] = Field(default_factory=list)
    include_toc: bool = True
    include_bibliography: bool = True
    include_evidence_annex: bool = False
    page_limit: int | None = Field(default=45, ge=1, le=500)
    enforce_page_limit: bool = False


class RenderDOCXProposalRequest(RenderProposalRequest):
    """Provides the RenderDOCXProposalRequest behaviour.

    Attributes:
        max_embedded_image_bytes (int).
    """
    max_embedded_image_bytes: int = Field(
        default=10_000_000,
        ge=1_000,
        le=50_000_000,
    )


@router.post("/{proposal_id}/render")
async def render_proposal(
    proposal_id: str,
    body: RenderProposalRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Render a proposal and persist both sanitised HTML and final PDF."""

    services = _services(request)
    if services.get("object_store") is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "object store unavailable",
        )
    scope_hash = hashlib.sha256(
        _scope(user).encode("utf-8")
    ).hexdigest()[:12]
    run_id = f"proposal-{scope_hash}-{proposal_id}"
    node = HorizonHTMLProposalRenderer(
        "render_proposal",
        body.model_dump(),
        services=services,
    )
    try:
        result = await node.run(
            {
                "inputs": {"SYSTEM.run_id": run_id},
                "session_id": _scope(user),
            },
            node.config.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


@router.post("/{proposal_id}/render/docx")
async def render_proposal_docx(
    proposal_id: str,
    body: RenderDOCXProposalRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Render a proposal as editable DOCX and persist its sanitised source."""

    services = _services(request)
    if services.get("object_store") is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "object store unavailable",
        )
    scope_hash = hashlib.sha256(
        _scope(user).encode("utf-8")
    ).hexdigest()[:12]
    run_id = f"proposal-{scope_hash}-{proposal_id}"
    node = HorizonDOCXProposalRenderer(
        "render_proposal_docx",
        body.model_dump(),
        services=services,
    )
    try:
        result = await node.run(
            {
                "inputs": {"SYSTEM.run_id": run_id},
                "session_id": _scope(user),
            },
            node.config.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result
