from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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