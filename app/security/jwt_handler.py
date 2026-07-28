from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from jose import JWTError, jwt

from app.config import settings


def create_access_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    now = datetime.now(timezone.utc)
    payload.update(
        {
            "exp": expire,
            "iat": now,
            "nbf": now,
            "jti": str(uuid.uuid4()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        }
    )
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require_exp": True,
                "require_iat": True,
                "require_nbf": True,
                "require_iss": True,
                "require_aud": True,
                "require_sub": True,
            },
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
