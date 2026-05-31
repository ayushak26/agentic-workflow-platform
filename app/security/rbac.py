from enum import Enum

class Role(str, Enum):
    ADMIN      = "admin"
    CONSULTANT = "consultant"
    VIEWER     = "viewer"

ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN:      {"workflow:run", "workflow:write", "workflow:read",
                      "eval:run", "user:manage"},
    Role.CONSULTANT: {"workflow:run", "workflow:write", "workflow:read", "eval:run"},
    Role.VIEWER:     {"workflow:read"},
}

def has_permission(role: Role, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())