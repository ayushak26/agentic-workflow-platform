from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.security.audit import read_audit_events
from app.security.dependencies import CurrentUser, require_consultant

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/session/{session_id}")
async def get_session_audit(
    session_id: str,
    request: Request,
    run_id: str | None = None,
    user: CurrentUser = Depends(require_consultant),
):
    if user.session_id is not None and user.session_id != session_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot read another session's audit trail")
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "audit store unavailable")
    try:
        events = await read_audit_events(db, session_id, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"session_id": session_id, "count": len(events), "events": events}