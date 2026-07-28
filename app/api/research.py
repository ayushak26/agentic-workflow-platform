"""Research-feature discovery endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.security.dependencies import CurrentUser, require_consultant

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/skills")
async def list_scientific_skills(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    del user
    catalog = request.app.state.services.get(
        "scientific_skill_catalog"
    )
    if catalog is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Scientific Agent Skills are disabled",
        )
    return {
        "skills": catalog.metadata(),
        "load_errors": catalog.load_errors,
    }
