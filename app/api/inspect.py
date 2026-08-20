"""Read-only corpus & retrieval inspection endpoints.

Two views, both for *understanding* the RAG layer, not for production traffic:

  GET  /inspect/chunks?collection_id=...&session_id=...
       Lists what is actually in Weaviate, grouped by source document, with
       per-chunk text and metadata. Answers "what got ingested and how was it
       split". Uses the v4 client's native fetch_objects so it does NOT depend
       on the exact signatures of your upsert/search helpers.

  POST /inspect/retrieve
       Runs a query through the REAL retrieve() pipeline and shows what comes
       back, with scores. Answers "given this query, what does hybrid search +
       rerank actually surface, and why". This is the demo-able view.

Wiring (one line in main.py / wherever routers are included):
    from app.api.inspect import router as inspect_router
    app.include_router(inspect_router)

The retrieve() call is isolated in _run_retrieval() with a >>> VERIFY marker —
if your retrieve() signature differs from the assumed
(query, *, filters, session_id, llm, top_k), adjust that one function only.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from app.security.dependencies import CurrentUser, require_permission
from app.security.guardrails import GuardrailViolation, check_workflow_inputs

router = APIRouter(prefix="/api/inspect", tags=["inspect"])


# --------------------------------------------------------------------------- #
# Response models                                                             #
# --------------------------------------------------------------------------- #
class ChunkView(BaseModel):
    chunk_id: str
    text: str
    token_count: int = 0
    chunk_index: int = 0
    doc_type: str = ""
    industry: str = ""
    language: str = ""
    session_id: str = ""
    collection_id: str = ""


class SourceGroup(BaseModel):
    source_path: str
    source_format: str = ""
    doc_type: str = ""
    chunk_count: int
    total_tokens: int
    chunks: list[ChunkView]


class ChunksResponse(BaseModel):
    collection_id: Optional[str] = None
    session_id: Optional[str] = None
    total_chunks: int
    source_count: int
    sources: list[SourceGroup]


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1)
    collection_id: str = Field(..., min_length=1)
    session_id: str = "default"
    doc_types: list[str] = Field(default_factory=list)
    top_k: int = 10


class RetrievedView(BaseModel):
    rank: int
    chunk_id: str = ""
    text: str
    score: Optional[float] = None
    doc_type: str = ""
    source_path: str = ""
    session_id: str = ""
    collection_id: str = ""


class RetrieveResponse(BaseModel):
    query: str
    collection_id: str
    session_id: str
    returned: int
    results: list[RetrievedView]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _get_weaviate(request: Request):
    """Return the WeaviateClient wrapper.

    We go through the module factory unconditionally: it returns the same
    singleton the rest of the app uses, already connected + schema-ensured.
    This avoids ambiguity over whether app.state.services stores the wrapper
    or the raw v4 client under some key.
    """
    from app.retrieval.weaviate_client import get_weaviate_client
    return get_weaviate_client()


def _native_collection(wv):
    """Return the v4 native collection handle for DocumentChunk.

    Mirrors WeaviateClient.count_chunks exactly: connect() returns the raw v4
    client (idempotent — returns the cached self._client), then
    client.collections.get(COLLECTION_NAME). Uses the module constant, not a
    settings field, because the wrapper hardcodes the collection name.
    """
    from app.retrieval.weaviate_client import COLLECTION_NAME

    raw = wv.connect()  # raw v4 WeaviateClient; cached after first call
    if raw is None:
        raise HTTPException(500, "Weaviate client not available")
    return raw.collections.get(COLLECTION_NAME)


# --------------------------------------------------------------------------- #
# View 1: what is ingested                                                     #
# --------------------------------------------------------------------------- #
@router.get("/chunks", response_model=ChunksResponse)
def list_chunks(
    request: Request,
    collection_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    limit: int = Query(2000, le=10000),
    user: CurrentUser = Depends(require_permission("workflow:read")),
) -> ChunksResponse:
    """List ingested chunks grouped by source document.

    Filters are optional: omit both to see the whole corpus. When set, they use
    the SAME boundary semantics as retrieval (collection_id + session_id AND-ed),
    so you can see exactly the slice a workflow run would retrieve against.
    """
    import weaviate.classes as wvc  # v4 query DSL

    allowed_session = user.session_id or user.username
    if session_id and session_id != allowed_session:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cannot inspect another session",
        )
    session_id = allowed_session

    wv = _get_weaviate(request)
    col = _native_collection(wv)

    # Build an AND filter from whatever was provided.
    conds = []
    if collection_id:
        conds.append(wvc.query.Filter.by_property("collection_id").equal(collection_id))
    if session_id:
        conds.append(wvc.query.Filter.by_property("session_id").equal(session_id))
    where = None
    if len(conds) == 1:
        where = conds[0]
    elif len(conds) > 1:
        where = wvc.query.Filter.all_of(conds)

    res = col.query.fetch_objects(limit=limit, filters=where)

    groups: dict[str, list[ChunkView]] = defaultdict(list)
    meta: dict[str, dict[str, str]] = {}
    total = 0
    for obj in res.objects:
        p = obj.properties
        src = str(p.get("source_path", "") or "(unknown source)")
        groups[src].append(
            ChunkView(
                chunk_id=str(p.get("chunk_id", "")),
                text=str(p.get("text", "")),
                token_count=int(p.get("token_count", 0) or 0),
                chunk_index=int(p.get("chunk_index", 0) or 0),
                doc_type=str(p.get("doc_type", "")),
                industry=str(p.get("industry", "")),
                language=str(p.get("language", "")),
                session_id=str(p.get("session_id", "")),
                collection_id=str(p.get("collection_id", "")),
            )
        )
        meta.setdefault(src, {
            "source_format": str(p.get("source_format", "")),
            "doc_type": str(p.get("doc_type", "")),
        })
        total += 1

    sources: list[SourceGroup] = []
    for src, chunks in groups.items():
        chunks.sort(key=lambda c: c.chunk_index)
        sources.append(SourceGroup(
            source_path=src,
            source_format=meta[src]["source_format"],
            doc_type=meta[src]["doc_type"],
            chunk_count=len(chunks),
            total_tokens=sum(c.token_count for c in chunks),
            chunks=chunks,
        ))
    sources.sort(key=lambda s: s.source_path)

    return ChunksResponse(
        collection_id=collection_id,
        session_id=session_id,
        total_chunks=total,
        source_count=len(sources),
        sources=sources,
    )


# --------------------------------------------------------------------------- #
# View 2: live retrieval preview                                              #
# --------------------------------------------------------------------------- #
async def _run_retrieval(
    request: Request,
    req: RetrieveRequest,
    user: CurrentUser,
):
    """Run the query through the canonical retrieval path.

    Delegates to ``services["retrieval_service"]`` — the same
    ``RetrievalService.retrieve()`` used by RAG, workflows and the
    Playground — so this view can never drift from production behaviour.
    ``req.collection_id`` is almost always a legacy CollectionConfig id
    rather than a Knowledge Studio ``col_...`` resource; RetrievalService
    handles that by falling back to its legacy-compatible path (no resolved
    Retrieval Profile/Index) whenever the id isn't a known logical Collection.

    Falls back to the older ``app.retrieval.retriever.retrieve`` compatibility
    adapter only when ``retrieval_service`` was never wired (e.g. Mongo/
    Weaviate unavailable at startup) — this route must still degrade rather
    than 503 in that case.
    """
    from app.retrieval.models import RetrievalFilters, RetrievalQuery

    services = getattr(request.app.state, "services", {}) or {}
    llm = services.get("llm") or services.get("llm_gateway")
    if llm is not None and hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="inspect:retrieve",
            session_id=user.session_id or user.username,
            node_id="retrieval_preview",
            ledger=services.get("cost_ledger"),
            semantic_cache=services.get("semantic_cache"),
            collection_id=req.collection_id or "default",
            workflow_name="Retrieval preview",
        )

    filters = RetrievalFilters(
        collection_id=req.collection_id,
        session_id=req.session_id,
        doc_types=req.doc_types or None,
    )

    retrieval_service = services.get("retrieval_service")
    if retrieval_service is not None:
        return await retrieval_service.retrieve(
            RetrievalQuery(query=req.query, filters=filters, top_k_candidates=req.top_k),
            owner_scope_id=req.session_id,
            llm=llm,
        )

    from app.retrieval.retriever import retrieve

    # Try the keyword-rich signature first; fall back to positional.
    try:
        result = await retrieve(
            req.query, filters=filters, session_id=req.session_id,
            llm=llm, top_k=req.top_k,
        )
    except TypeError:
        result = await retrieve(req.query, filters=filters, llm=llm)
    return result


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_preview(
    request: Request,
    req: RetrieveRequest,
    user: CurrentUser = Depends(require_permission("workflow:read")),
) -> RetrieveResponse:
    """Run a query through hybrid search + rerank and show the ranked chunks."""
    allowed_session = user.session_id or user.username
    if req.session_id not in {"", "default", allowed_session}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cannot retrieve from another session",
        )
    try:
        safe_query = check_workflow_inputs({"query": req.query}).value["query"]
    except GuardrailViolation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    req = req.model_copy(
        update={"query": safe_query, "session_id": allowed_session}
    )
    try:
        result = await _run_retrieval(request, req, user)
    except Exception as e:  # surface the real error to the UI, don't 500 silently
        raise HTTPException(400, f"retrieval failed: {type(e).__name__}: {e}")

    # RetrievalResult.chunks is the conventional shape; be defensive about names.
    chunks = getattr(result, "chunks", None)
    if chunks is None and isinstance(result, list):
        chunks = result
    chunks = chunks or []

    views: list[RetrievedView] = []
    for i, c in enumerate(chunks, start=1):
        get = (lambda k, d="": getattr(c, k, d)) if not isinstance(c, dict) \
            else (lambda k, d="": c.get(k, d))
        views.append(RetrievedView(
            rank=i,
            chunk_id=str(get("chunk_id", "")),
            text=str(get("text", "")),
            score=get("score", None) if get("score", None) is not None else get("rerank_score", None),
            doc_type=str(get("doc_type", "")),
            source_path=str(get("source_path", "")),
            session_id=str(get("session_id", "")),
            collection_id=str(get("collection_id", "")),
        ))

    return RetrieveResponse(
        query=req.query,
        collection_id=req.collection_id,
        session_id=req.session_id,
        returned=len(views),
        results=views,
    )
