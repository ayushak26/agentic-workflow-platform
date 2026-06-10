from typing import Any

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
import yaml
from pathlib import Path
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.nodes.registry import NodeRegistry
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow, HITLResumeError
from app.runtime.loader import load_workflow_from_string
from app.security.dependencies import require_permission, require_consultant, require_admin

router = APIRouter(prefix="/api", tags=["workflows"])


@router.get("/")
async def list_workflows():
    return {"workflows": []}

@router.get("/node-types")
def list_node_types(payload: dict = Depends(require_admin)):
    """The Workflow Builder UI calls this to populate the palette and forms."""
    return NodeRegistry.manifest()

@router.get("/node-types/{node_type}/models")
async def allowed_models(node_type: str):
    """Returns the default allowed model list for a node type."""
    from app.llm.registry import _PREFIX_ROUTES, _FALLBACK_MODEL
    all_models = list(_PREFIX_ROUTES.keys()) + list(_FALLBACK_MODEL.values())
    return {"node_type": node_type, "allowed_models": sorted(set(all_models))}


class RunRequest(BaseModel):
    workflow_yaml: str
    inputs: dict = {}
    session_id: str | None = None
    collection_id: str = "default"
    run_id: str | None = None

@router.post("/workflows/run")
async def run(req: RunRequest, request: Request, payload: dict = Depends(require_consultant)):
    try:
        spec = load_workflow_from_string(req.workflow_yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    services = getattr(request.app.state, "services", {})
    try:
        return await run_workflow(
            spec, req.inputs, req.session_id,collection_id=req.collection_id,services=services, run_id=req.run_id
        )
    except Exception as e:
        # The workflow failed at runtime. run_workflow already published a
        # run_failed event to the bus, so the Cockpit WS already shows the failure.
        # We return a clean 200 here so the POST doesn't bubble to an unhandled 500,
        # which Starlette generates OUTSIDE the CORS middleware — that 500 would lack
        # Access-Control-Allow-Origin and the browser would report "Failed to fetch",
        # masking the real error.
        return {"status": "failed", "run_id": req.run_id, "error": str(e)}


class ResumeRequest(BaseModel):
    decision: dict[str, Any]


@router.post("/workflows/{run_id}/resume")
async def resume(run_id: str, req: ResumeRequest):
    """Resume doesn't need services — the cached compiled graph in
    _PAUSED_GRAPHS already has services bound into every node instance."""
    try:
        return await resume_workflow(run_id, req.decision)
    except HITLResumeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # A node failed after resume. Return clean so the POST doesn't become an
        # unhandled 500 (which Starlette emits outside CORS → "Failed to fetch").
        return {"status": "failed", "run_id": run_id, "error": str(e)}
    
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
    try:
        # Reuse the loader we wrote in Phase 4 — it parses + validates.
        load_workflow_from_string(req.yaml)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid workflow spec: {e}")

    # Defensive name check — no path traversal, no shell-unsafe chars.
    if not req.name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="name must be alphanumeric + _ -")

    (WORKFLOWS_DIR / f"{req.name}.yaml").write_text(req.yaml)
    return {"ok": True, "name": req.name}

@router.websocket("/ws/runs/{run_id}")
async def ws_run(ws: WebSocket, run_id: str):
    """Live event stream for one workflow run.

    Wire format: one JSON envelope per message, see RunEvent.to_json().
    Client lifecycle: ws.connect → receive events → ws closes after run_completed/run_failed.
    On reconnect the client should call GET /api/runs/{run_id} to refetch state.
    """
    services = ws.app.state.services
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

@router.get("/files")
def get_file(key: str, request: Request, download: bool = False):
    """Stream a file from object storage by key (used by the Output Viewer).
    POC: serves any workflow-scoped key. Phase 11 adds session-scoped access."""
    if not key.startswith("workflows/"):
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
