from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.security.dependencies import CurrentUser, require_consultant
from app.security.audit import read_audit_events
from app.workflow.run_history import get_run, list_runs

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _scope(user: CurrentUser) -> str:
    return getattr(user, "session_id", None) or user.username


@router.get("/mine")
async def my_runs(
    request: Request,
    limit: int = 50,
    user: CurrentUser = Depends(require_consultant),
):
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run store unavailable")
    try:
        runs = await list_runs(db, _scope(user), limit=limit)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"count": len(runs), "runs": runs}


@router.get("/mine/{run_id}")
async def my_run_detail(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run store unavailable")
    scope = _scope(user)
    try:
        run = await get_run(db, scope, run_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    audit = await read_audit_events(db, scope, run_id=run_id)
    return {"run": run, "audit": audit}