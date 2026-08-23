"""MCP servers as first-class connections.

A workflow says *"call this CRM capability"*. It must never say *"make this
Dynamics Web API HTTP request"*, and it must never carry a credential. This
module is what makes both true: a server is a named connection, declared once by
the deployment, and a workflow references it by id.

    workflow YAML          server_id: dynamics365
                           tool: find_account
                                    │
    MCPServerConnection    transport, command, args, secret refs, policy
                                    │
    secrets                resolved from the environment at launch time,
                           never read from — or written back into — a workflow

The same registry serves the Builder's connection list, the client's launch
specs, and the policy layer's allowlist, so what an author can pick, what the
platform will run, and what policy permits cannot drift apart.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.observability.logging import get_logger

log = get_logger(__name__)

#: Environment variable holding a JSON array of server definitions. Definitions
#: carry secret *references* (env var names), never secret values, so this can
#: live in ordinary non-secret configuration.
SERVERS_ENV = "MCP_SERVERS"

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: How a tool's effect on the outside world is classified. The Builder renders
#: it on the canvas and the policy layer gates on it.
OperationClass = Literal["read", "write", "destructive", "unknown"]

#: What may run without a human in front of it.
WritePolicy = Literal[
    "read_only",        # writes are refused outright
    "require_approval", # writes need a human review upstream
    "allow",            # writes may run unattended — an explicit decision
]


class MCPToolPolicy(BaseModel):
    """Per-tool overrides, set by the deployment rather than by the server.

    Deliberately separate from the server's own annotations. A server declaring
    `readOnlyHint: true` is a *hint from the thing being governed* — the MCP
    specification says as much — so a deployment must be able to state that
    `update_account` is a write no matter what the server claims.
    """

    model_config = ConfigDict(extra="forbid")

    operation: OperationClass | None = None
    requires_approval: bool | None = None
    #: Roles permitted to invoke it. Empty means "whatever the server allows".
    allowed_roles: list[str] = Field(default_factory=list)
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    description: str = ""
    #: Business-language guidance shown in the Builder's tool info panel.
    typical_uses: list[str] = Field(default_factory=list)


class MCPServerConnection(BaseModel):
    """One configured MCP server."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str = ""
    description: str = ""

    transport: Literal["stdio"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)

    #: Environment variables passed to the subprocess, as {NAME: ENV_VAR_NAME}.
    #: Values are read from the platform's own environment at launch. A literal
    #: secret here would be a configuration error, so only references are
    #: accepted — see `resolve_environment`.
    environment_secret_refs: dict[str, str] = Field(default_factory=dict)
    #: Non-secret environment values (mode switches, URLs that are not secrets).
    environment: dict[str, str] = Field(default_factory=dict)

    #: Tools an author may use. Empty means "every tool the server exposes",
    #: which is the right default for a first-party server and the wrong one for
    #: anything reachable by a third party — hence it is configurable.
    tool_allowlist: list[str] = Field(default_factory=list)
    tool_denylist: list[str] = Field(default_factory=list)
    write_policy: WritePolicy = "require_approval"
    #: Roles allowed to invoke any tool on this server.
    allowed_roles: list[str] = Field(default_factory=list)
    tool_policies: dict[str, MCPToolPolicy] = Field(default_factory=dict)

    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_result_bytes: int = Field(default=256_000, ge=1_000, le=10_000_000)
    max_read_retries: int = Field(default=1, ge=0, le=5)

    #: Presentation only: which deployment this connection points at, so a demo
    #: against fixtures is never mistaken for production.
    environment_label: str = ""
    #: True when this connection is backed by fixtures rather than a live
    #: system. Surfaced prominently in the Builder — a green "Connected" badge
    #: over a mock is how a demo becomes a lie.
    is_mock: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("id")
    @classmethod
    def id_is_safe(cls, value: str) -> str:
        """Compute the id is safe.

        Args:
            value (str): Value to process.

        Returns:
            str: The is safe.
        """
        if not _SAFE_ID.fullmatch(value):
            raise ValueError(
                f"server id {value!r} must be lowercase alphanumeric with _ or -"
            )
        return value

    @model_validator(mode="after")
    def stdio_needs_a_command(self) -> "MCPServerConnection":
        """Compute the stdio needs a command.

        Returns:
            'MCPServerConnection': The needs a command.
        """
        if self.transport == "stdio" and not self.command.strip():
            raise ValueError(f"server {self.id!r} needs a command to launch")
        return self

    @model_validator(mode="after")
    def secrets_are_references(self) -> "MCPServerConnection":
        """Reject a literal secret pasted where a reference belongs.

        An env-var *name* is uppercase and short. Anything containing a space,
        a URL, or a long opaque token is a value that someone pasted into
        configuration by mistake — and it would then sit in whatever stores that
        configuration.
        """
        for name, reference in self.environment_secret_refs.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", reference):
                raise ValueError(
                    f"server {self.id!r}: environment_secret_refs[{name!r}] must "
                    "be the NAME of an environment variable, not its value"
                )
        return self

    @property
    def label(self) -> str:
        """The label."""
        return self.display_name or self.id

    def resolve_environment(self) -> dict[str, str]:
        """Build the subprocess environment, reading secrets at launch time.

        A missing secret is logged and omitted rather than raising: the server
        subprocess is the thing that knows whether it can work without a given
        variable (a mock Dynamics server needs no client secret at all), and it
        reports a usable error if it cannot.
        """
        resolved = dict(self.environment)
        for name, reference in self.environment_secret_refs.items():
            value = os.environ.get(reference, "")
            if not value:
                log.warning(
                    "mcp.registry.secret_missing",
                    server=self.id,
                    variable=name,
                    reference=reference,
                )
                continue
            resolved[name] = value
        return resolved

    def describe(self) -> dict[str, Any]:
        """Builder-facing description. Never includes a resolved secret —
        only which variables are expected and whether they are present."""
        return {
            "id": self.id,
            "display_name": self.label,
            "description": self.description,
            "transport": self.transport,
            "environment_label": self.environment_label,
            "is_mock": self.is_mock,
            "write_policy": self.write_policy,
            "tool_allowlist": list(self.tool_allowlist),
            "timeout_seconds": self.timeout_seconds,
            "credentials": [
                {
                    "variable": name,
                    "reference": reference,
                    "configured": bool(os.environ.get(reference, "")),
                }
                for name, reference in sorted(self.environment_secret_refs.items())
            ],
        }

    def permits_tool(self, tool_name: str) -> bool:
        """Compute the permits tool.

        Args:
            tool_name (str): The tool name.

        Returns:
            bool: The tool.
        """
        if tool_name in self.tool_denylist:
            return False
        if self.tool_allowlist and tool_name not in self.tool_allowlist:
            return False
        return True

    def policy_for(self, tool_name: str) -> MCPToolPolicy:
        """Compute the policy for.

        Args:
            tool_name (str): The tool name.

        Returns:
            MCPToolPolicy: The for.
        """
        return self.tool_policies.get(tool_name, MCPToolPolicy())


class MCPServerRegistry:
    """The set of configured servers, keyed by id."""

    def __init__(self, servers: dict[str, MCPServerConnection] | None = None):
        """Initialize the MCPServerRegistry.

        Args:
            servers (dict[str, MCPServerConnection] | None): The servers (optional, default None).
        """
        self._servers = dict(servers or {})
        self._health: dict[str, dict[str, Any]] = {}

    def __contains__(self, server_id: str) -> bool:
        """Implement the ``__contains__`` protocol.

        Args:
            server_id (str): The server id.

        Returns:
            bool: The result.
        """
        return server_id in self._servers

    def __len__(self) -> int:
        """Implement the ``__len__`` protocol.

        Returns:
            int: The result.
        """
        return len(self._servers)

    @property
    def ids(self) -> tuple[str, ...]:
        """The ids."""
        return tuple(self._servers)

    def add(self, connection: MCPServerConnection) -> None:
        """Add the result.

        Args:
            connection (MCPServerConnection): The connection.
        """
        self._servers[connection.id] = connection

    def get(self, server_id: str) -> MCPServerConnection | None:
        """Return the result.

        Args:
            server_id (str): The server id.

        Returns:
            MCPServerConnection | None: The result.
        """
        return self._servers.get(server_id)

    def require(self, server_id: str) -> MCPServerConnection:
        """Compute the require.

        Args:
            server_id (str): The server id.

        Returns:
            MCPServerConnection: The result.
        """
        found = self._servers.get(server_id)
        if found is None:
            raise KeyError(
                f"MCP server {server_id!r} is not configured "
                f"(available: {sorted(self._servers) or 'none'})"
            )
        return found

    def all(self) -> list[MCPServerConnection]:
        """Compute the all.

        Returns:
            list[MCPServerConnection]: The result.
        """
        return [self._servers[key] for key in sorted(self._servers)]

    def record_health(
        self,
        server_id: str,
        *,
        healthy: bool,
        tool_count: int = 0,
        error: str | None = None,
    ) -> None:
        """Record the health.

        Args:
            server_id (str): The server id.
            healthy (bool): The healthy.
            tool_count (int): The tool count (optional, default 0).
            error (str | None): Error value or message (optional, default None).
        """
        self._health[server_id] = {
            "healthy": healthy,
            "tool_count": tool_count,
            "error": error,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    def health(self, server_id: str) -> dict[str, Any]:
        """Compute the health.

        Args:
            server_id (str): The server id.

        Returns:
            dict[str, Any]: The result.
        """
        return self._health.get(
            server_id,
            {"healthy": None, "tool_count": 0, "error": None, "checked_at": None},
        )

    def describe_all(self) -> list[dict[str, Any]]:
        """Compute the describe all.

        Returns:
            list[dict[str, Any]]: The all.
        """
        return [
            {**connection.describe(), "status": self.health(connection.id)}
            for connection in self.all()
        ]


def load_servers(raw: str | None = None) -> dict[str, MCPServerConnection]:
    """Parse configured servers from JSON.

    A malformed entry is skipped with a log line rather than raising: one bad
    connection definition must not stop the API from starting, and preflight
    reports the missing server against the workflow that actually needs it.
    """
    source = raw if raw is not None else os.environ.get(SERVERS_ENV, "")
    if not source.strip():
        return {}

    try:
        entries = json.loads(source)
    except json.JSONDecodeError as error:
        log.error("mcp.registry.unparseable", error=str(error))
        return {}
    if not isinstance(entries, list):
        log.error("mcp.registry.not_a_list")
        return {}

    servers: dict[str, MCPServerConnection] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            connection = MCPServerConnection(**entry)
        except Exception as error:
            log.error(
                "mcp.registry.invalid_server",
                server=entry.get("id"),
                error=str(error),
            )
            continue
        servers[connection.id] = connection
    return servers
