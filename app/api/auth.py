from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from app.config import settings
from app.security.jwt_handler import create_access_token
from app.security.rbac import Role
from app.security.dependencies import get_current_user, CurrentUser
from app.security.users import authenticate_local_user

router = APIRouter(prefix="/auth", tags=["auth"])

_DEV_USERS = {
    settings.dev_bypass_username: {
        "password": settings.dev_bypass_password,
        "role": Role.ADMIN,
    }
}


class Token(BaseModel):
    access_token: str
    token_type: str
    username: str
    role: str


@router.post("/token", response_model=Token)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
):
    user = None
    if (
        settings.environment.strip().lower() != "production"
        and settings.dev_bypass_enabled
    ):
        user = _DEV_USERS.get(form.username)
        if user is not None:
            user = {"username": form.username, "role": user["role"]} \
                if user["password"] == form.password else None
    else:
        db = getattr(request.app.state, "services", {}).get("audit_db")
        if db is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is unavailable",
            )
        user = await authenticate_local_user(
            db,
            username=form.username,
            password=form.password,
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = user["username"]
    token = create_access_token(
        {
            "sub": username,
            "role": user["role"].value,
            "session_id": username,
        }
    )
    return Token(
        access_token=token,
        token_type="bearer",
        username=username,
        role=user["role"].value,
    )


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "username": user.username,
        "role": user.role.value,
        "session_id": user.session_id,
    }
