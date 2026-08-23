"""Dependencies module.

Part of the security layer: auth, rbac, middleware, guardrails, and entity protection.

Public symbols: CurrentUser, get_current_user, get_current_user_cookie_or_header, require_permission.
"""
from fastapi import Cookie,Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.security.jwt_handler import decode_token
from app.security.rbac import Role, has_permission

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


class CurrentUser:
    """Provides the CurrentUser behaviour."""
    def __init__(self, username: str, role: Role, session_id: str | None = None):
        """Initialize the CurrentUser.

        Args:
            username (str): Username value.
            role (Role): User role.
            session_id (str | None): Session scope the record belongs to (optional, default None).
        """
        self.username = username
        self.role = role
        self.session_id = session_id

def _user_from_token(token: str | None) -> CurrentUser:
    """Internal helper for the user from token step.

    Args:
        token (str | None): Token value.

    Returns:
        CurrentUser: The from token.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(token)
        return CurrentUser(
            username=payload["sub"],
            role=Role(payload.get("role", Role.CONSULTANT)),
            session_id=payload.get("session_id"),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    access_token: str | None = Cookie(default=None),
) -> CurrentUser:
    """Return the current user.

    Args:
        token (str | None): Token value (optional, default Depends(oauth2_scheme)).
        access_token (str | None): The access token (optional, default Cookie(default=None)).

    Returns:
        CurrentUser: The current user.
    """
    return _user_from_token(token or access_token)


async def get_current_user_cookie_or_header(
    token: str | None = Depends(oauth2_scheme),
    access_token: str | None = Cookie(default=None),
) -> CurrentUser:
    """Header first (API clients), then HttpOnly cookie (browser EventSource)."""
    return _user_from_token(token or access_token)

def require_permission(permission: str):
    """Compute the require permission.

    Args:
        permission (str): Permission name.
    """
    async def checker(
        user: CurrentUser = Depends(get_current_user_cookie_or_header),
    ) -> CurrentUser:
        """Compute the checker.

        Args:
            user (CurrentUser): Authenticated current user (optional, default ...).

        Returns:
            CurrentUser: The result.
        """
        if not has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission}",
            )
        return user
    return checker



require_consultant = require_permission("workflow:run")
require_admin      = require_permission("user:manage")