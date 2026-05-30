from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import yaml
from pathlib import Path
from fastapi import WebSocket, WebSocketDisconnect

from app.nodes.registry import NodeRegistry
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow, HITLResumeError
from app.runtime.loader import load_workflow_from_string

router = APIRouter(prefix="/api", tags=["workflows"])


@router.get("/node-types")
def list_node_types():
    """The Workflow Builder UI calls this to populate the palette and forms."""
    return NodeRegistry.manifest()


class RunRequest(BaseModel):
    workflow_yaml: str
    inputs: dict = {}
    session_id: str | None = None


@router.post("/workflows/run")
async def run(req: RunRequest, request: Request):
    try:
        spec = load_workflow_from_string(req.workflow_yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    # Pull the services bag built in main.py's lifespan
    services = getattr(request.app.state, "services", {})
    result = await run_workflow(spec, req.inputs, req.session_id, services=services)
    return result


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
