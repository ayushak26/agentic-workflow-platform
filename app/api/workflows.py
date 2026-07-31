import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Depends, Header
from pydantic import BaseModel, Field
import yaml
from pathlib import Path
from fastapi.responses import Response, StreamingResponse

from app.nodes.registry import NodeRegistry
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow_durable, HITLResumeError
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import (
    PreflightCheck,
    PreflightIssue,
    PreflightSeverity,
    WorkflowPreflightReport,
    preflight_workflow_for_run,
    preflight_workflow_yaml,
)
from app.security.dependencies import (
    require_permission, require_consultant, require_admin, CurrentUser,
)
# Phase 11A — run history + durable retry checkpoints
from app.workflow.orchestration import (
    finalize_run_result,
    record_run_failure,
    run_and_finalize,
    start_new_run_record,
)
from app.security.audit import write_audit_event, HITL_EVENT
from app.workflow.file_inputs import (
    WorkflowFileInputError,
    validate_workflow_inputs,
)

router = APIRouter(prefix="/api", tags=["workflows"])


async def _reserve_run_id(
    services: dict[str, Any],
    *,
    run_id: str,
    session_id: str,
) -> str:
    """Claim a client-supplied run_id for one tenant, idempotently.

    Two backing stores with different meanings:
      - run_history (Mongo): COMMITTED runs. A hit means a real run exists.
      - redis (SET NX):       PENDING reservations, before the run is committed.

    Isolation rule: a caller only ever learns about run_ids it owns.
      * owner re-reserving a committed id          -> 409 (already exists, yours)
      * a different tenant hitting a committed id   -> 404 (hidden, as if absent)
      * owner re-reserving a pending id             -> ok (idempotent re-POST)
      * a different tenant hitting a pending id     -> 409 (claimed, cannot take)

    Returns run_id on success; raises HTTPException(404|409) otherwise.

    Only client-SUPPLIED run_ids need this. A freshly minted uuid4 has
    negligible collision probability and no racing caller, so the run route
    skips reservation for auto-generated ids.
    """
    db = services.get("audit_db")
    redis = services.get("redis")

    # 1. Committed runs win. Check ownership without disclosing across tenants.
    if db is not None:
        existing = await db["run_history"].find_one({"run_id": run_id})
        if existing is not None:
            if existing.get("session_id") == session_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"run_id already exists: {run_id}",
                )
            # Owned by another tenant — do not reveal that it exists.
            raise HTTPException(status_code=404, detail="run_id not found")

    # 2. No committed run. Try to claim a pending reservation in Redis.
    if redis is not None:
        key = f"run_reservation:{run_id}"
        claimed = await redis.set(key, session_id, nx=True)
        if not claimed:
            owner = await redis.get(key)
            if isinstance(owner, bytes):
                owner = owner.decode()
            if owner == session_id:
                return run_id           # idempotent: same tenant, same id
            raise HTTPException(
                status_code=409,
                detail=f"run_id already reserved: {run_id}",
            )

    return run_id


def _scope(user: CurrentUser, body_session: str | None) -> str:
    """The value a run is scoped to for storage + retrieval.

    MUST match _scope() in app/api/runs.py, which reads:
        user.session_id or user.username
    A caller may repeat that value in the body, but cannot override the token
    boundary. Allowing a different body value creates a run the same caller
    cannot retrieve later and weakens tenant isolation.
    """
    token_scope = getattr(user, "session_id", None) or user.username
    if body_session and body_session != token_scope:
        raise HTTPException(
            status_code=400,
            detail="session_id must match the authenticated session",
        )
    return token_scope


@router.get("/")
async def list_workflows():
    return {"workflows": []}


@router.get("/node-types")
def list_node_types(user: CurrentUser = Depends(require_admin)):
    """The Workflow Builder UI calls this to populate the palette and forms."""
    return NodeRegistry.manifest()


@router.get("/node-types/{node_type}/models")
async def allowed_models(node_type: str):
    """Returns the default allowed model list for a node type."""
    from app.runtime.schema import DEFAULT_LLM_MODELS

    return {
        "node_type": node_type,
        "allowed_models": list(DEFAULT_LLM_MODELS),
    }


class RunRequest(BaseModel):
    workflow_yaml: str
    inputs: dict = Field(default_factory=dict)
    session_id: str | None = None
    collection_id: str = "default"
    run_id: str | None = None


class ValidateWorkflowRequest(BaseModel):
    workflow_yaml: str
    inputs: dict[str, Any] | None = None
    check_services: bool = False


def _preflight_http_detail(report: WorkflowPreflightReport) -> dict[str, Any]:
    return {
        "message": (
            f"Workflow preflight found {len(report.errors)} blocking "
            "error(s). No nodes or LLMs were run."
        ),
        "preflight": report.model_dump(mode="json"),
    }


@router.post("/workflows/validate")
async def validate_workflow(
    req: ValidateWorkflowRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Validate a workflow without running a node or consuming LLM tokens."""

    services = getattr(request.app.state, "services", {})
    if req.check_services:
        report = await preflight_workflow_for_run(
            req.workflow_yaml,
            provided_inputs=req.inputs or {},
            services=services,
            probe_services=True,
            require_run_history=True,
        )
        if report.valid:
            try:
                spec = load_workflow_from_string(req.workflow_yaml)
                await validate_workflow_inputs(
                    spec.inputs,
                    req.inputs or {},
                    session_id=_scope(user, None),
                    object_store=services.get("object_store"),
                )
            except WorkflowFileInputError as exc:
                report.issues.append(
                    PreflightIssue(
                        code="WORKFLOW_INPUT_INVALID",
                        severity=PreflightSeverity.ERROR,
                        message=str(exc),
                        path="inputs",
                        suggestion=(
                            "Re-upload the affected file or correct the input "
                            "before starting the workflow."
                        ),
                    )
                )
                report.checks.append(
                    PreflightCheck(
                        name="input_files",
                        status="failed",
                        detail=(
                            "Input references were checked without executing "
                            "a node."
                        ),
                    )
                )
                report.refresh()
            else:
                report.checks.append(
                    PreflightCheck(
                        name="input_files",
                        status="passed",
                        detail=(
                            "Input references exist, belong to this session, "
                            "and match the workflow contract."
                        ),
                    )
                )
    else:
        report = preflight_workflow_yaml(
            req.workflow_yaml,
            provided_inputs=req.inputs,
            services=services,
            compile_graph=True,
        )
    return report.model_dump(mode="json")


@router.post("/workflows/run")
async def run(req: RunRequest, request: Request, user: CurrentUser = Depends(require_consultant)):
    services = getattr(request.app.state, "services", {})
    preflight = await preflight_workflow_for_run(
        req.workflow_yaml,
        provided_inputs=req.inputs,
        services=services,
        probe_services=True,
        require_run_history=True,
    )
    if not preflight.valid:
        raise HTTPException(
            status_code=422,
            detail=_preflight_http_detail(preflight),
        )

    try:
        spec = load_workflow_from_string(req.workflow_yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    db = services.get("audit_db")   # Phase 11A — Mongo handle for run history + audit
    session = _scope(user, req.session_id)   # matched to runs.py read scope
    try:
        validated_inputs = await validate_workflow_inputs(
            spec.inputs,
            req.inputs,
            session_id=session,
            object_store=services.get("object_store"),
        )
    except WorkflowFileInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Client-supplied run_ids are reserved per-tenant (idempotent re-POST safe,
    # cross-tenant claims blocked). Auto-generated ids need no reservation.
    if req.run_id:
        run_id = await _reserve_run_id(
            services, run_id=req.run_id, session_id=session,
        )
    else:
        run_id = str(uuid.uuid4())

    # Create the durable record BEFORE execution. This makes running and paused
    # workflows visible in Run History and gives node hooks a document to patch.
    await start_new_run_record(
        db,
        run_id=run_id,
        session=session,
        spec=spec,
        workflow_yaml=req.workflow_yaml,
        inputs=validated_inputs,
        collection_id=req.collection_id,
    )

    # A clean 200 (not a raised exception) either way: run_workflow already
    # published a run_failed event to the bus, so the Cockpit WS already shows
    # the failure, and an unhandled 500 here would be generated by Starlette
    # OUTSIDE the CORS middleware — that response would lack
    # Access-Control-Allow-Origin and the browser would report "Failed to
    # fetch", masking the real error.
    return await run_and_finalize(
        run_workflow(
            spec,
            validated_inputs,
            session,
            collection_id=req.collection_id,
            services=services,
            run_id=run_id,
        ),
        db=db,
        run_id=run_id,
        session=session,
    )


class ResumeRequest(BaseModel):
    decision: dict[str, Any]
    session_id: str | None = None   # Phase 11A — Cockpit may send it; else derived from token


@router.post("/workflows/{run_id}/resume")
async def resume(
    run_id: str,
    req: ResumeRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),   # Phase 11A — resume was UNAUTHENTICATED before
):
    """Resume doesn't need services for execution — the cached compiled graph in
    _PAUSED_GRAPHS already has services bound into every node instance. It does
    need the services dict for the audit/history Mongo handle, and it now
    requires auth (a known run_id must not be resumable by an anonymous caller)."""
    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    session = _scope(user, req.session_id)
    actor = user.username   # real JWT subject (CurrentUser, not a dict)

    try:
        result = await resume_workflow_durable(
            run_id,
            req.decision,
            services=services,
            session_id=session,
            actor=actor,
        )
    except HITLResumeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # A node failed after resume.
        return await record_run_failure(db, run_id=run_id, session=session, error=e)

    # Phase 11A — HITL audit event: the human decision, now that session is in scope.
    action = req.decision.get("decision")
    if db is not None and action in HITL_EVENT:
        audit_payload: dict[str, Any] = {}
        if action == "reject" and result.get("reason"):
            audit_payload["reason"] = result.get("reason")
        if action == "edit":
            edited = req.decision.get("edited_content") or {}
            source_document = edited.get("source_document") or {}
            audit_payload = {
                "source": edited.get("source", "editor"),
                "content_chars": len(str(edited.get("text") or "")),
                "document_name": source_document.get("name"),
                "document_sha256": source_document.get("sha256"),
                "content_recorded": False,
            }
        await write_audit_event(
            db, run_id, session,
            node_id=(
                result.get("node_id")
                or result.get("resumed_node_id")
                or req.decision.get("node_id")
                or "unknown"
            ),
            event_type=HITL_EVENT[action],
            actor=actor,
            payload=audit_payload,
        )

    # Persist both re-pause and terminal resume states.
    return await finalize_run_result(
        result,
        db=db,
        run_id=run_id,
        session=session,
        record_rejection_reason=True,
    )


WORKFLOWS_DIR = Path("workflows")


@router.get("/workflows")
def list_workflows():
    """Library view in the UI calls this to render the saved-workflow list."""
    out = []
    for p in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except yaml.YAMLError:
            continue
        out.append({
            "name": p.stem,
            "description": data.get("description", ""),
            "use_case": data.get("use_case", "generic"),
            "version": data.get("version", "1.0"),
            "node_count": len(data.get("nodes", [])),
        })
    return out


@router.get("/workflows/by-name/{name}")
def get_workflow(name: str):
    """Builder calls this to load an existing workflow for editing.

    Note the /by-name/ segment: it disambiguates from /workflows/{run_id}/resume.
    A path collision is avoidable in FastAPI by ordering, but explicit beats clever.
    """
    p = WORKFLOWS_DIR / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"workflow '{name}' not found")
    return {"name": name, "yaml": p.read_text()}


class SaveWorkflowRequest(BaseModel):
    name: str
    yaml: str


@router.post("/workflows/save")
def save_workflow(req: SaveWorkflowRequest):
    """Builder calls this on Save. Validates the YAML parses to a WorkflowSpec
    before writing — bad input never lands on disk."""
    report = preflight_workflow_yaml(req.yaml, compile_graph=True)
    if not report.valid:
        raise HTTPException(
            status_code=422,
            detail=_preflight_http_detail(report),
        )

    # Defensive name check — no path traversal, no shell-unsafe chars.
    if not req.name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="name must be alphanumeric + _ -")

    (WORKFLOWS_DIR / f"{req.name}.yaml").write_text(req.yaml)
    return {"ok": True, "name": req.name}


def _sse_message(
    *,
    event: str,
    data: dict[str, Any],
    event_id: int | None = None,
) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(
        "data: "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )
    return "\n".join(lines) + "\n\n"


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    last_event_id: int | None = Header(
        default=None,
        alias="Last-Event-ID",
    ),
    user: CurrentUser = Depends(require_consultant),
):
    """Authenticated Server-Sent Events stream for one workflow run."""

    services = request.app.state.services
    event_bus = services["event_bus"]
    session_id = _scope(user, None)
    queue = await event_bus.subscribe(
        run_id,
        session_id,
        after_event_id=last_event_id,
    )

    async def generate():
        try:
            yield _sse_message(
                event="ready",
                data={"run_id": run_id},
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=(
                            request.app.state.services.get(
                                "sse_heartbeat_seconds",
                                15.0,
                            )
                        ),
                    )
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                yield _sse_message(
                    event=event.type,
                    data=event.to_json(),
                    event_id=event.event_id,
                )
                if event.terminal:
                    break
        finally:
            await event_bus.unsubscribe(
                run_id,
                queue,
                session_id,
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/files")
def get_file(key: str, request: Request, download: bool = False):
    """Stream a file from object storage by key (used by the Output Viewer).
    POC: serves any workflow- or evidence-scoped key. Phase 11 adds
    session-scoped access."""
    if not (key.startswith("workflows/") or key.startswith("evidence/")):
        raise HTTPException(status_code=400, detail="only workflow-scoped keys are served")
    store = getattr(request.app.state, "services", {}).get("object_store")
    if store is None:
        raise HTTPException(status_code=503, detail="object store unavailable")
    try:
        data = store.get_bytes(key)
    except Exception:
        raise HTTPException(status_code=404, detail="file not found")

    media_type = "application/pdf" if key.endswith(".pdf") else "application/octet-stream"
    headers = {}
    if download:
        filename = key.rsplit("/", 1)[-1]
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=data, media_type=media_type, headers=headers)
