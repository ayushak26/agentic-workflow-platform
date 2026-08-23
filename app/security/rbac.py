"""Rbac module.

Part of the security layer: auth, rbac, middleware, guardrails, and entity protection.

Public symbols: Role, has_permission.
"""
from enum import Enum

class Role(str, Enum):
    """Enumeration of Role values."""
    ADMIN      = "admin"
    CONSULTANT = "consultant"
    VIEWER     = "viewer"

ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN:      {"workflow:run", "workflow:write", "workflow:read",
                      "eval:run", "user:manage",
                      "knowledge:read", "knowledge:write", "rag:query", "rag:write"},
    Role.CONSULTANT: {"workflow:run", "workflow:write", "workflow:read", "eval:run",
                      "knowledge:read", "knowledge:write", "rag:query", "rag:write"},
    Role.VIEWER:     {"workflow:read", "knowledge:read", "rag:query"},
}

def has_permission(role: Role, permission: str) -> bool:
    """Return whether permission.

    Args:
        role (Role): User role.
        permission (str): Permission name.

    Returns:
        bool: True when permission.
    """
    return permission in ROLE_PERMISSIONS.get(role, set())