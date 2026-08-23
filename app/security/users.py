"""Mongo-backed local users used by the IONOS production deployment."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.security.passwords import hash_password, verify_password
from app.security.rbac import Role

_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


async def ensure_user_indexes(db: Any) -> None:
    """Ensure the user indexes.

    Args:
        db (Any): Mongo database handle.
    """
    await db["users"].create_index("username", unique=True)
    await db["users"].create_index([("active", 1), ("role", 1)])


def validate_username(username: str) -> str:
    """Validate the username.

    Args:
        username (str): Username value.

    Returns:
        str: The username.
    """
    normalized = username.strip().lower()
    if not _USERNAME.fullmatch(normalized):
        raise ValueError(
            "Username must be 3-64 characters using letters, numbers, '.', '_' or '-'"
        )
    return normalized


async def upsert_local_user(
    db: Any,
    *,
    username: str,
    password: str,
    role: Role,
    active: bool = True,
) -> None:
    """Upsert the local user.

    Args:
        db (Any): Mongo database handle.
        username (str): Username value.
        password (str): Password value.
        role (Role): User role.
        active (bool): Active flag (optional, default True).
    """
    normalized = validate_username(username)
    now = datetime.now(timezone.utc)
    await db["users"].update_one(
        {"username": normalized},
        {
            "$set": {
                "password_hash": hash_password(password),
                "role": role.value,
                "active": active,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def authenticate_local_user(
    db: Any,
    *,
    username: str,
    password: str,
) -> dict[str, Any] | None:
    """Authenticate the local user.

    Args:
        db (Any): Mongo database handle.
        username (str): Username value.
        password (str): Password value.

    Returns:
        dict[str, Any] | None: The local user.
    """
    try:
        normalized = validate_username(username)
    except ValueError:
        normalized = "invalid-user"
    user = await db["users"].find_one(
        {"username": normalized, "active": True},
        {"_id": 0},
    )
    if not verify_password(password, (user or {}).get("password_hash")):
        return None
    try:
        role = Role(user["role"])
    except (KeyError, ValueError):
        return None
    return {"username": normalized, "role": role}
