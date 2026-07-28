import time
import uuid
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field
import yaml
from pathlib import Path
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.responses import StreamingResponse

from app.nodes.registry import NodeRegistry
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow_durable, HITLResumeError
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import (
    WorkflowPreflightReport,
    preflight_workflow_for_run,
    preflight_workflow_yaml,
)
from app.security.dependencies import require_permission, require_consultant, require_admin, CurrentUser
from app.security.guardrails import GuardrailViolation, check_workflow_inputs
# Phase 11A — run history + durable retry checkpoints
from app.workflow.run_history import (
    initialize_run_checkpoint,
    mark_checkpoint_status,
    upsert_run,
)
from app.security.audit import write_audit_event, HITL_EVENT
from app.workflow.file_inputs import (
    WorkflowFileInputError,
    validate_workflow_inputs,
)

router = APIRouter(prefix="/api", tags=["workflows"])


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
async def root_workflows(
    _user: CurrentUser = Depends(require_permission("workflow:read")),
):
    return {"workflows": []}

@router.get("/node-types")
def list_node_types(user: CurrentUser = Depends(require_admin)):
    """The Workflow Builder UI calls this to populate the palette and forms."""
    return NodeRegistry.manifest()

@router.get("/node-types/{node_type}/models")
async def allowed_models(
    node_type: str,
    _user: CurrentUser = Depends(require_permission("workflow:read")),
):
    """Returns the default allowed model list for a node type."""
    from app.runtime.schema import DEFAULT_LLM_MODELS

    return {
        "node_type": node_type,
        "allowed_models": list(DEFAULT_LLM_MODELS),
    }


class RunRequest(BaseModel):
    workflow_yaml: str = Field(min_length=1, max_length=2_000_000)
    inputs: dict = Field(default_factory=dict)
    session_id: str | None = None
    collection_id: str = "default"
    run_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$",
    )


class ValidateWorkflowRequest(BaseModel):
    workflow_yaml: str = Field(min_length=1, max_length=2_000_000)
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
    session = _scope(user, req.session_id)
    if req.run_id:
        await _reserve_run_id(
            services,
            run_id=req.run_id,
            session_id=session,
        )
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
    try:
        validated_inputs = await validate_workflow_inputs(
            spec.inputs,
            req.inputs,
            session_id=session,
            object_store=services.get("object_store"),
        )
    except WorkflowFileInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        validated_inputs = check_workflow_inputs(validated_inputs).value
    except GuardrailViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    node_types = {n.id: n.type for n in spec.nodes}   # for UI colour-coding
    run_id = req.run_id or str(uuid.uuid4())
    started_at = time.time()

    # Create the durable record BEFORE execution. This makes running and paused
    # workflows visible in Run History and gives node hooks a document to patch.
    if db is not None:
        await upsert_run(
            db,
            run_id,
            session,
            workflow_name=spec.name,
            status="running",
            inputs=validated_inputs,
            variables={
                variable.name: variable.value
                for variable in spec.static_variables
            },
            workflow_yaml=req.workflow_yaml,
            started_at=started_at,
            node_count=len(spec.nodes),
            completed_node_count=0,
            node_types=node_types,
            attempt=1,
        )
        await initialize_run_checkpoint(
            db,
            run_id=run_id,
            session_id=session,
            workflow_yaml=req.workflow_yaml,
            inputs=validated_inputs,
            collection_id=req.collection_id,
        )

    try:
        result = await run_workflow(
            spec,
            validated_inputs,
            session,
            collection_id=req.collection_id,
            services=services,
            run_id=run_id,
        )
    except Exception as e:
        # The workflow failed at runtime. run_workflow already published a
        # run_failed event to the bus, so the Cockpit WS already shows the failure.
        # We return a clean 200 here so the POST doesn't bubble to an unhandled 500,
        # which Starlette generates OUTSIDE the CORS middleware — that 500 would lack
        # Access-Control-Allow-Origin and the browser would report "Failed to fetch",
        # masking the real error.
        # Phase 11A — persist a failed run record (history write never breaks the response).
        if db is not None:
            await upsert_run(
                db,
                run_id,
                session,
                status="failed",
                ended_at=time.time(),
                error=str(e)[:500],
            )
            await mark_checkpoint_status(
                db,
                run_id=run_id,
                session_id=session,
                status="failed",
            )
        return {"status": "failed", "run_id": run_id, "error": str(e)}

    # Persist pause and terminal states. Node hooks have already written every
    # completed output, so this final write is a consistent full-state snapshot.
    if db is not None:
        state = result.get("state", {})
        run_status = result.get("status", "completed")
        await upsert_run(
            db,
            run_id,
            session,
            status=run_status,
            outputs=(
                state.get("node_outputs", {})
                if run_status != "paused"
                else None
            ),
            ended_at=(
                time.time()
                if run_status in {"completed", "rejected", "failed"}
                else None
            ),
            completed_node_count=len(state.get("node_outputs", {})),
        )
        await mark_checkpoint_status(
            db,
            run_id=run_id,
            session_id=session,
            status=run_status,
        )
    return result


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
        # A node failed after resume. Return clean so the POST doesn't become an
        # unhandled 500 (which Starlette emits outside CORS → "Failed to fetch").
        if db is not None:
            await upsert_run(
                db,
                run_id,
                session,
                status="failed",
                ended_at=time.time(),
                error=str(e)[:500],
            )
            await mark_checkpoint_status(
                db,
                run_id=run_id,
                session_id=session,
                status="failed",
            )
        return {"status": "failed", "run_id": run_id, "error": str(e)}

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
    if db is not None:
        state = result.get("state", {})
        run_status = result.get("status", "completed")
        await upsert_run(
            db,
            run_id,
            session,
            status=run_status,
            outputs=(
                state.get("node_outputs", {})
                if run_status != "paused"
                else None
            ),
            ended_at=(
                time.time()
                if run_status in {"completed", "rejected", "failed"}
                else None
            ),
            completed_node_count=len(state.get("node_outputs", {})),
            error=result.get("reason"),
        )
        await mark_checkpoint_status(
            db,
            run_id=run_id,
            session_id=session,
            status=run_status,
        )
    return result
    
WORKFLOWS_DIR = Path("workflows")


@router.get("/workflows")
def list_workflows(
    _user: CurrentUser = Depends(require_permission("workflow:read")),
):
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
def get_workflow(
    name: str,
    _user: CurrentUser = Depends(require_permission("workflow:read")),
):
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
def save_workflow(
    req: SaveWorkflowRequest,
    _user: CurrentUser = Depends(require_permission("workflow:write")),
):
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

class WebSocketTicketRequest(BaseModel):
    run_id: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$",
    )


async def _reserve_run_id(
    services: dict[str, Any],
    *,
    run_id: str,
    session_id: str,
) -> None:
    """Atomically bind a client-generated run id to one authenticated tenant."""

    db = services.get("audit_db")
    if db is None:
        raise HTTPException(status_code=503, detail="Run store unavailable")
    existing = await db["run_history"].find_one(
        {"run_id": run_id},
        {"_id": 0, "session_id": 1},
    )
    if existing is not None:
        # Do not disclose whether another tenant owns a guessed run id.
        if existing.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(status_code=409, detail="Run id already exists")

    redis = services.get("redis")
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="Run reservation service is unavailable",
        )
    key = f"awp:run-reservation:{run_id}"
    reserved = await redis.set(key, session_id, ex=300, nx=True)
    if reserved:
        return
    owner = await redis.get(key)
    if isinstance(owner, bytes):
        owner = owner.decode("utf-8", errors="replace")
    if owner != session_id:
        raise HTTPException(status_code=409, detail="Run id is unavailable")


@router.post("/ws/tickets")
async def create_websocket_ticket(
    body: WebSocketTicketRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    redis = getattr(request.app.state, "services", {}).get("redis")
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="Live event authentication is unavailable",
        )
    session_id = user.session_id or user.username
    db = getattr(request.app.state, "services", {}).get("audit_db")
    existing = (
        await db["run_history"].find_one(
            {"run_id": body.run_id},
            {"_id": 0, "session_id": 1},
        )
        if db is not None
        else None
    )
    if existing is not None:
        if existing.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail="Run not found")
    else:
        await _reserve_run_id(
            getattr(request.app.state, "services", {}),
            run_id=body.run_id,
            session_id=session_id,
        )
    ticket = uuid.uuid4().hex + uuid.uuid4().hex
    payload = {
        "run_id": body.run_id,
        "session_id": session_id,
        "username": user.username,
    }
    await redis.set(
        f"awp:ws-ticket:{ticket}",
        json.dumps(payload, separators=(",", ":")),
        ex=60,
        nx=True,
    )
    return {"ticket": ticket, "expires_in": 60}


@router.websocket("/ws/runs/{run_id}")
async def ws_run(ws: WebSocket, run_id: str, ticket: str = ""):
    """Live event stream for one workflow run.

    Wire format: one JSON envelope per message, see RunEvent.to_json().
    Client lifecycle: ws.connect → receive events → ws closes after run_completed/run_failed.
    On reconnect the client should call GET /api/runs/{run_id} to refetch state.
    """
    services = ws.app.state.services
    redis = services.get("redis")
    if redis is None or not ticket:
        await ws.close(code=4401, reason="Authentication required")
        return
    raw_ticket = await redis.getdel(f"awp:ws-ticket:{ticket}")
    if not raw_ticket:
        await ws.close(code=4401, reason="Invalid or expired ticket")
        return
    try:
        ticket_data = json.loads(raw_ticket)
    except (TypeError, ValueError, json.JSONDecodeError):
        await ws.close(code=4401, reason="Invalid ticket")
        return
    if ticket_data.get("run_id") != run_id:
        await ws.close(code=4403, reason="Ticket scope mismatch")
        return
    bus = services["event_bus"]

    await ws.accept()
    q = await bus.subscribe(run_id)
    try:
        while True:
            evt = await q.get()
            await ws.send_json(evt.to_json())
            if evt.type in {"run_completed", "run_failed"}:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await bus.unsubscribe(run_id, q)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    """Authenticated SSE alternative for clients that do not use WebSockets."""

    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    session_id = user.session_id or user.username
    if db is None:
        raise HTTPException(status_code=503, detail="Run store unavailable")
    owned = await db["run_history"].find_one(
        {"run_id": run_id, "session_id": session_id},
        {"_id": 1},
    )
    if owned is None:
        raise HTTPException(status_code=404, detail="Run not found")

    bus = services.get("event_bus")
    if bus is None:
        raise HTTPException(status_code=503, detail="Event service unavailable")

    async def generate():
        queue = await bus.subscribe(run_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event.to_json(), separators=(',', ':'))}\n\n"
                if event.type in {"run_completed", "run_failed"}:
                    break
        finally:
            await bus.unsubscribe(run_id, queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/files")
async def get_file(
    key: str,
    request: Request,
    download: bool = False,
    user: CurrentUser = Depends(require_permission("workflow:read")),
):
    """Stream a file from object storage by key (used by the Output Viewer).
    POC: serves any workflow-scoped key. Phase 11 adds session-scoped access."""
    if not key.startswith("workflows/"):
        raise HTTPException(status_code=400, detail="only workflow-scoped keys are served")
    key_parts = key.split("/", 2)
    if len(key_parts) != 3 or not key_parts[1]:
        raise HTTPException(status_code=404, detail="file not found")
    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    session_id = user.session_id or user.username
    if db is None or await db["run_history"].find_one(
        {"run_id": key_parts[1], "session_id": session_id},
        {"_id": 1},
    ) is None:
        raise HTTPException(status_code=404, detail="file not found")
    store = services.get("object_store")
    if store is None:
        raise HTTPException(status_code=503, detail="object store unavailable")
    try:
        data = await asyncio.to_thread(store.get_bytes, key)
    except Exception:
        raise HTTPException(status_code=404, detail="file not found")

    media_type = "application/pdf" if key.endswith(".pdf") else "application/octet-stream"
    headers = {}
    if download:
        filename = key.rsplit("/", 1)[-1]
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=data, media_type=media_type, headers=headers)
