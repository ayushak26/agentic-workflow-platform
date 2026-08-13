"""Built-in MCP connections, and how the integration service is assembled.

The in-repo servers (Eurskem retrieval, Dynamics 365) get connection definitions
here so they carry the same policy, classification and audit treatment as any
third-party server declared through `MCP_SERVERS`. There is no "trusted because
it is ours" path: the Dynamics connection is read-only by default and its write
tools require a human review, whoever wrote the server.
"""
from __future__ import annotations

from typing import Any

from app.config import Settings, settings
from app.integrations.operations import ExternalOperationLedger
from app.mcp.dynamics.tools import READ_ONLY_TOOLS, TOOL_DEFINITIONS
from app.mcp.registry import (
    MCPServerConnection,
    MCPServerRegistry,
    MCPToolPolicy,
    load_servers,
)
from app.mcp.service import MCPIntegrationService
from app.observability.logging import get_logger

log = get_logger(__name__)


def _dynamics_tool_policies() -> dict[str, MCPToolPolicy]:
    """State each Dynamics tool's class as deployment policy.

    Belt and braces: the server already declares annotations and the name
    heuristics would classify these correctly anyway. Declaring them here means
    the platform's answer to "is `create_lead` a write?" does not depend on the
    server being honest or on a regex being clever.
    """
    return {
        definition["name"]: MCPToolPolicy(
            operation=definition["operation"],
            typical_uses=definition.get("typical_uses", []),
        )
        for definition in TOOL_DEFINITIONS
    }


def dynamics_connection(app_settings: Settings = settings) -> MCPServerConnection:
    is_mock = app_settings.dynamics_mcp_mode.strip().lower() != "live"
    return MCPServerConnection(
        id="dynamics365",
        display_name="Microsoft Dynamics 365",
        description=(
            "Customer accounts, contacts, opportunities, orders and activities "
            "from Dynamics 365 / Dataverse."
        ),
        transport="stdio",
        command="python",
        args=["-m", "app.mcp.dynamics.server"],
        # Named for the Builder's connection panel, which shows whether each is
        # configured. The values themselves are passed to the subprocess by
        # app/mcp/client.py and never travel any further.
        environment_secret_refs={
            "DYNAMICS_CLIENT_ID": "DYNAMICS_CLIENT_ID",
            "DYNAMICS_CLIENT_SECRET": "DYNAMICS_CLIENT_SECRET",
            "DYNAMICS_TENANT_ID": "DYNAMICS_TENANT_ID",
        },
        environment={"DYNAMICS_MODE": app_settings.dynamics_mcp_mode},
        # Every tool the server exposes is permitted, but writes still need a
        # human review — see write_policy. To make this connection genuinely
        # read-only (§25), set tool_allowlist to READ_ONLY_TOOLS and give the
        # Entra application user a read-only security role.
        tool_allowlist=[],
        write_policy="require_approval",
        tool_policies=_dynamics_tool_policies(),
        environment_label="Demo fixtures" if is_mock else "Live tenant",
        is_mock=is_mock,
        timeout_seconds=45.0,
    )


def read_only_dynamics_connection(
    app_settings: Settings = settings,
) -> MCPServerConnection:
    """A least-privilege variant, for deployments that want CRM context only.

    Pairs with an Entra application user holding a read-only security role: the
    allowlist stops the platform from *asking*, and the security role stops
    Dynamics from *allowing*. Defence at both ends, because either alone fails
    open under a configuration mistake.
    """
    connection = dynamics_connection(app_settings)
    return connection.model_copy(
        update={
            "id": "dynamics365_readonly",
            "display_name": "Microsoft Dynamics 365 (read only)",
            "tool_allowlist": list(READ_ONLY_TOOLS),
            "write_policy": "read_only",
        }
    )


def build_registry(app_settings: Settings = settings) -> MCPServerRegistry:
    """Assemble every configured connection.

    Built-ins first, then anything declared through `MCP_SERVERS`. A configured
    server may not shadow a built-in id — that would let configuration silently
    redirect `dynamics365` at a different process.
    """
    registry = MCPServerRegistry()

    registry.add(
        MCPServerConnection(
            id="eurskem",
            display_name="Eurskem Knowledge Base",
            description="Hybrid retrieval over the platform's own document corpus.",
            command="python",
            args=["-m", "app.mcp.server"],
            write_policy="read_only",
        )
    )

    if app_settings.dynamics_mcp_enabled:
        registry.add(dynamics_connection(app_settings))

    if app_settings.paper_search_mcp_enabled:
        registry.add(
            MCPServerConnection(
                id="paper-search-mcp",
                display_name="Scholarly Paper Search",
                description="Literature search across public scholarly sources.",
                command="python",
                args=["-m", "app.mcp.paper_search_server"],
                write_policy="read_only",
            )
        )

    for connection in load_servers().values():
        if connection.id in registry:
            log.warning(
                "mcp.connections.shadowed",
                server=connection.id,
                detail="a configured server cannot replace a built-in connection",
            )
            continue
        registry.add(connection)

    return registry


def build_mcp_service(
    *,
    client: Any,
    db: Any = None,
    app_settings: Settings = settings,
) -> MCPIntegrationService:
    return MCPIntegrationService(
        registry=build_registry(app_settings),
        client=client,
        ledger=ExternalOperationLedger(db, collection="mcp_operations"),
    )
