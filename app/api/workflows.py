from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.nodes.registry import NodeRegistry
from app.runtime.loader import load_workflow_from_string
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow, HITLResumeError
from typing import Any

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
async def run(req: RunRequest):
    try:
        spec = load_workflow_from_string(req.workflow_yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    result = await run_workflow(spec, req.inputs, req.session_id)
    return result

class ResumeRequest(BaseModel):
    decision: dict[str, Any]   # {"decision": "approve"} or richer

@router.post("/workflows/{run_id}/resume")
async def resume(run_id: str, req: ResumeRequest):
    try:
        return await resume_workflow(run_id, req.decision)
    except HITLResumeError as e:
        raise HTTPException(status_code=404, detail=str(e))
