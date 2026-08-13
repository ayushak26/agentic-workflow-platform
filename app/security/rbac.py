from enum import Enum

class Role(str, Enum):
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
    return permission in ROLE_PERMISSIONS.get(role, set())