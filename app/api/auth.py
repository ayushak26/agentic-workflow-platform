from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.config import settings
from app.security.jwt_handler import create_access_token
from app.security.rbac import Role
from app.security.dependencies import require_consultant, CurrentUser
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


class Identity(BaseModel):
    username: str
    role: str
    session_id: str | None = None


def _set_auth_cookie(response: Response, token: str) -> None:
    """Attach the JWT as an HttpOnly cookie.

    path="/" so the browser sends it to both /api/* (data routes) and
    /auth/* (identity rehydration). SameSite=lax is sufficient because the
    SPA is served same-origin with the API via the Vite proxy; cross-site
    POSTs will not carry the cookie, which is the CSRF mitigation for the POC.
    secure is enabled only in production (dev runs over plain http://localhost).
    """
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.environment.strip().lower() == "production",
        path="/",
        max_age=settings.access_token_expire_minutes * 60,
    )


@router.post("/token", response_model=Token)
async def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
):
    user = None
    if (
        settings.environment.strip().lower() != "production"
        and settings.dev_bypass_enabled
    ):
        dev = _DEV_USERS.get(form.username)
        if dev is not None and dev["password"] == form.password:
            user = {"username": form.username, "role": dev["role"]}
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
    _set_auth_cookie(response, token)
    return Token(
        access_token=token,
        token_type="bearer",
        username=username,
        role=user["role"].value,
    )


@router.get("/me", response_model=Identity)
async def me(user: CurrentUser = Depends(require_consultant)):
    """Rehydrate identity from the auth cookie after a page refresh.

    The SPA loses its in-memory token on reload; this lets it recover the
    session from the HttpOnly cookie without forcing a re-login.
    """
    return Identity(
        username=user.username,
        role=user.role.value,
        session_id=user.session_id,
    )


@router.post("/logout")
async def logout(response: Response):
    """Clear the auth cookie. Must match the path the cookie was set with."""
    response.delete_cookie(key="access_token", path="/")
    return {"ok": True}