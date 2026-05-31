from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from app.config import settings
from app.security.jwt_handler import create_access_token
from app.security.rbac import Role
from app.security.dependencies import get_current_user, CurrentUser

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
async def login(form: OAuth2PasswordRequestForm = Depends()):
    if not settings.dev_bypass_enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Use Microsoft SSO in production",
        )
    user = _DEV_USERS.get(form.username)
    if not user or user["password"] != form.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_access_token({"sub": form.username, "role": user["role"].value})
    return Token(
        access_token=token,
        token_type="bearer",
        username=form.username,
        role=user["role"].value,
    )


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {"username": user.username, "role": user.role}