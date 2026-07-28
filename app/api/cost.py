from fastapi import APIRouter, Request, Depends
from app.security.dependencies import get_current_user, CurrentUser

router = APIRouter(prefix="/api/cost", tags=["cost"])


@router.get("/run/{run_id}")
async def run_cost(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    ledger = request.app.state.services.get("cost_ledger")
    if not ledger:
        return {"error": "cost ledger unavailable"}
    session_id = user.session_id or user.username
    return ledger.run_summary(run_id, session_id)


@router.get("/session/{session_id}")
async def session_cost(
    session_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    ledger = request.app.state.services.get("cost_ledger")
    if not ledger:
        return {"error": "cost ledger unavailable"}
    expected = user.session_id or user.username
    if session_id != expected:
        from fastapi import HTTPException, status

        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cannot read another session's cost data",
        )
    return ledger.session_summary(session_id)
