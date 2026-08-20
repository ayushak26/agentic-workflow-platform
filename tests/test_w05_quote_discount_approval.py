"""End-to-end coverage for w05's approval gate on create_opportunity.

sales_manager_review/finance_review (HumanInLoopAgent) wire straight into
create_opportunity_* (MCPToolAgent) with a plain edge and no router in
between. That is safe: the compiler auto-wires every HumanInLoopAgent node's
outgoing edges through a synthetic router that sends `reject` to `END`
regardless of what the workflow declares (app/runtime/compiler.py's
`_hitl_router`) — a reject can never reach a downstream node here, full
stop, no per-workflow wiring required to get that guarantee.

What w05 previously did rely on the workflow author for is *which* review
authorizes the write: both create_opportunity_* nodes had
`allow_unattended_write: true`, a blanket bypass that (in a workflow with
several independent HITL branches, like this one's Sales-Manager/Finance
split) does not actually check that the *matching* review approved — only
that some review output somewhere says approve/edit. That is now
`approved_by: sales_manager_review` / `approved_by: finance_review`
instead, scoping each write's approval check to its own review.

This test proves both properties end-to-end: reject never reaches the
write (the platform-wide guarantee), and approve does reach it, honoring
the specific `approved_by` review rather than any review anywhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.integrations.operations import ExternalOperationLedger
from app.mcp.registry import MCPServerConnection, MCPServerRegistry
from app.mcp.service import MCPIntegrationService
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow_durable
from app.runtime.loader import load_workflow

WORKFLOW = load_workflow(Path("workflows/w05_quote_discount_approval.yaml"))

START_INPUT = {
    "company_name": "Acme Pumps",
    "product_summary": "Centrifugal pump service contract",
    "deal_value": 5000,
    "discount_percentage": 25,  # > 10 → needs_sales_manager_approval only
}


class FakeTool:
    def __init__(self, name: str, *, input_schema: dict | None = None):
        self.name = name
        self.title = None
        self.description = ""
        self.inputSchema = input_schema or {"type": "object", "properties": {}}
        self.outputSchema = None
        self.annotations = None
        self.meta = None


class FakeResult:
    def __init__(self, structured: dict):
        self.structuredContent = structured
        self.content = []
        self.isError = False


class FakeClient:
    """One transport, shared by every connection in the registry below —
    real deployments route each server to a different process, but nothing
    in MCPToolAgent's contract depends on that, and the tools involved don't
    overlap in ways that would hide a cross-server mistake.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.running_servers = (
            "dynamics365_finance_scm",
            "dynamics365",
            "business_records",
        )
        self._tools = [
            FakeTool("find_customer", input_schema={
                "type": "object",
                "properties": {"customer_name": {"type": "string"}},
            }),
            FakeTool("find_account", input_schema={
                "type": "object",
                "properties": {"company_name": {"type": "string"}},
            }),
            FakeTool("create_opportunity", input_schema={
                "type": "object",
                "properties": {
                    "account_id": {"type": "string"},
                    "name": {"type": "string"},
                    "estimated_value": {"type": "number"},
                },
                "required": ["name", "estimated_value"],
            }),
        ]

    async def list_tools(self, server: str):
        del server
        return self._tools

    async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
        self.calls.append({"tool": name, "arguments": arguments, "server": server})
        if name == "find_customer":
            return FakeResult({"customers": [{"key_account": False}], "count": 1})
        if name == "find_account":
            return FakeResult({"accounts": [{"account_id": "ACC-1"}], "count": 1})
        if name == "create_opportunity":
            return FakeResult({"opportunity": {"id": "OPP-1"}})
        raise AssertionError(f"unexpected tool call: {name}")


def _mcp_service(client: FakeClient) -> MCPIntegrationService:
    registry = MCPServerRegistry()
    for server_id in (
        "dynamics365_finance_scm",
        "dynamics365",
        "business_records",
    ):
        registry.add(MCPServerConnection(id=server_id, command="stub"))
    return MCPIntegrationService(
        registry=registry, client=client, ledger=ExternalOperationLedger()
    )


async def _run_to_sales_manager_review(run_id: str, client: FakeClient) -> dict:
    services = {
        "mcp": _mcp_service(client),
        "langgraph_checkpointer": MemorySaver(),
    }
    result = await run_workflow(
        WORKFLOW, inputs=START_INPUT, services=services, run_id=run_id
    )
    assert result["status"] == "paused"
    return services


async def test_rejecting_the_sales_manager_review_never_creates_the_opportunity():
    client = FakeClient()
    services = await _run_to_sales_manager_review("w05-reject", client)

    resumed = await resume_workflow_durable(
        "w05-reject", {"decision": "reject", "reason": "over budget"}, services=services
    )

    assert resumed["status"] == "rejected"
    outputs = resumed.get("state", {}).get("node_outputs", {})
    assert "create_opportunity_sales_mgr" not in outputs, (
        "the compiler's reject-halts-the-run guarantee must hold for w05"
    )
    assert not any(call["tool"] == "create_opportunity" for call in client.calls), (
        "create_opportunity was actually called on a rejection"
    )


async def test_approving_the_sales_manager_review_creates_the_opportunity():
    client = FakeClient()
    services = await _run_to_sales_manager_review("w05-approve", client)

    resumed = await resume_workflow_durable(
        "w05-approve", {"decision": "approve"}, services=services
    )

    assert resumed["status"] == "completed"
    outputs = resumed.get("state", {}).get("node_outputs", {})
    assert outputs["create_opportunity_sales_mgr"]["status"] == "ok", (
        "approved_by: sales_manager_review should have satisfied the gate"
    )
    assert any(
        call["tool"] == "create_opportunity" for call in client.calls
    ), "the approved write should actually have run"
