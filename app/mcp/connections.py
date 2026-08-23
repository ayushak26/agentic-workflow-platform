"""Built-in MCP connections, and how the integration service is assembled.

The in-repo servers (Eurskem retrieval, Dynamics 365) get connection definitions
here so they carry the same policy, classification and audit treatment as any
third-party server declared through `MCP_SERVERS`. There is no "trusted because
it is ours" path: the Dynamics connection is read-only by default and its write
tools require a human review, whoever wrote the server.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings, settings
from app.integrations.operations import ExternalOperationLedger
from app.mcp.business_records.tools import TOOL_DEFINITIONS as BUSINESS_RECORDS_TOOL_DEFINITIONS
from app.mcp.d365_finance.tools import TOOL_DEFINITIONS as FNO_MOCK_TOOL_DEFINITIONS
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

#: The Finance & Operations MCP server (mcp-servers/d365-finance-scm-mcp) is a
#: separate Node/TypeScript subproject, built with `npm run build` into `dist/`.
_FNO_MCP_ENTRYPOINT = (
    Path(__file__).resolve().parents[2]
    / "mcp-servers"
    / "d365-finance-scm-mcp"
    / "dist"
    / "src"
    / "index.js"
)

#: Operation class per tool exposed by that server — see its src/tools.ts.
_FNO_TOOL_OPERATIONS: dict[str, str] = {
    "erp_health": "read",
    "erp_list_entity_sets": "read",
    "erp_describe_entity": "read",
    "erp_query": "read",
    "erp_get_record": "read",
    "erp_create_record": "write",
    "erp_update_record": "write",
    "erp_delete_record": "destructive",
}


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
    """Compute the dynamics connection.

    Args:
        app_settings (Settings): The app settings (optional, default settings).

    Returns:
        MCPServerConnection: The connection.
    """
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


def finance_scm_connection(app_settings: Settings = settings) -> MCPServerConnection:
    """The Dynamics 365 Finance & Operations connector.

    Distinct system from `dynamics_connection` above: F&O customers, sales
    orders and inventory rather than Dataverse CRM accounts/opportunities.

    `live` mode spawns the real Node/TypeScript server (generic erp_query/
    erp_get_record adapter over a real F&O tenant). `mock` mode (the default)
    spawns app/mcp/d365_finance/server.py, a fixture-backed Python server
    exposing the narrow business tools that server's own README recommends
    building on top of it — that layer isn't built on the live server yet, so
    unlike the Dataverse CRM connection's mock/live pair, the two modes here
    expose a different tool vocabulary. Building that layer on the real server
    with matching tool names would close the gap.
    """
    is_mock = app_settings.fno_mcp_mode.strip().lower() != "live"
    if is_mock:
        return MCPServerConnection(
            id="dynamics365_finance_scm",
            display_name="Microsoft Dynamics 365 Finance & Supply Chain",
            description=(
                "Customers, sales orders, inventory and account ownership from "
                "Dynamics 365 Finance & Operations."
            ),
            transport="stdio",
            command="python",
            args=["-m", "app.mcp.d365_finance.server"],
            tool_allowlist=[],
            write_policy="read_only",
            tool_policies={
                definition["name"]: MCPToolPolicy(operation=definition["operation"])
                for definition in FNO_MOCK_TOOL_DEFINITIONS
            },
            environment_label="Demo fixtures",
            is_mock=True,
            timeout_seconds=45.0,
        )

    return MCPServerConnection(
        id="dynamics365_finance_scm",
        display_name="Microsoft Dynamics 365 Finance & Supply Chain",
        description=(
            "Customers, sales orders, inventory and other public entities from "
            "Dynamics 365 Finance & Operations OData."
        ),
        transport="stdio",
        command="node",
        args=[str(_FNO_MCP_ENTRYPOINT)],
        environment_secret_refs={
            "FNO_TENANT_ID": "FNO_TENANT_ID",
            "FNO_CLIENT_ID": "FNO_CLIENT_ID",
            "FNO_CLIENT_SECRET": "FNO_CLIENT_SECRET",
        },
        environment={
            "FNO_BASE_URL": app_settings.fno_base_url,
            "FNO_ALLOW_WRITES": "true" if app_settings.fno_allow_writes else "false",
            "FNO_ALLOW_DELETES": "true" if app_settings.fno_allow_deletes else "false",
            "FNO_READ_ENTITY_ALLOWLIST": app_settings.fno_read_entity_allowlist,
            "FNO_WRITE_ENTITY_ALLOWLIST": app_settings.fno_write_entity_allowlist,
            "FNO_DELETE_ENTITY_ALLOWLIST": app_settings.fno_delete_entity_allowlist,
            "FNO_ENTITY_ALIASES_JSON": app_settings.fno_entity_aliases_json,
        },
        # Every tool the server exposes is permitted, but writes/deletes still
        # need a human review here regardless of the server's own FNO_ALLOW_*
        # gates — two independent locks, as with the Dataverse CRM connection.
        tool_allowlist=[],
        write_policy="require_approval",
        tool_policies={
            name: MCPToolPolicy(operation=operation)
            for name, operation in _FNO_TOOL_OPERATIONS.items()
        },
        environment_label="Live tenant",
        is_mock=False,
        timeout_seconds=45.0,
    )


def business_records_connection(app_settings: Settings = settings) -> MCPServerConnection:
    """A real, persistent MySQL database — genuine Lookup/Create/Update tools.

    Unlike the fixture-backed mocks above, this connection's backend is a
    live database (seeded from both `d365_finance` and `dynamics` fixture
    data via `app.mcp.business_records.seed`), so a customer_search here
    reflects whatever create_case/create_order calls have actually run
    against it, not a static JSON snapshot. Writes still require a human
    review, exactly like every other write-capable connection.
    """
    return MCPServerConnection(
        id="business_records",
        display_name="Business Records",
        description=(
            "Customers, orders, quotes, products and support cases — a "
            "shared read/write store seeded from Finance & Operations and "
            "CRM data, for cases where a workflow needs to look up or "
            "record business objects directly."
        ),
        transport="stdio",
        command="python",
        args=["-m", "app.mcp.business_records.server"],
        environment={
            "BUSINESS_RECORDS_MYSQL_HOST": app_settings.business_records_mysql_host,
            "BUSINESS_RECORDS_MYSQL_PORT": str(app_settings.business_records_mysql_port),
            "BUSINESS_RECORDS_MYSQL_USER": app_settings.business_records_mysql_user,
            "BUSINESS_RECORDS_MYSQL_PASSWORD": app_settings.business_records_mysql_password,
            "BUSINESS_RECORDS_MYSQL_DATABASE": app_settings.business_records_mysql_database,
        },
        tool_allowlist=[],
        write_policy="require_approval",
        tool_policies={
            definition["name"]: MCPToolPolicy(operation=definition["operation"])
            for definition in BUSINESS_RECORDS_TOOL_DEFINITIONS
        },
        environment_label="Live database",
        is_mock=False,
        timeout_seconds=45.0,
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

    if app_settings.fno_mcp_enabled:
        registry.add(finance_scm_connection(app_settings))

    if app_settings.business_records_mcp_enabled:
        registry.add(business_records_connection(app_settings))

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
    """Build the mcp service.

    Args:
        client (Any): Client instance.
        db (Any): Mongo database handle (optional, default None).
        app_settings (Settings): The app settings (optional, default settings).

    Returns:
        MCPIntegrationService: The mcp service.
    """
    return MCPIntegrationService(
        registry=build_registry(app_settings),
        client=client,
        ledger=ExternalOperationLedger(db, collection="mcp_operations"),
    )
