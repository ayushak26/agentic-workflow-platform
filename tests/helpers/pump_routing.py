"""Shared harness for the pump customer-routing workflows (Levels 1–3).

Only two things are faked: the LLM (so a test can state exactly what the model
understood) and the MCP *transport* (so no subprocess is spawned). Everything
else runs for real — the rules engine, the routers, the MCP policy gate, and
the same `app/mcp/d365_finance` handlers the stdio server exposes, against the
same bundled fixtures.

That split is deliberate. These are routing tests: the interesting question is
never "did the model parse this sentence correctly" but "given these facts, did
the case reach the team that can resolve it". Scripting the extraction makes the
routing assertions exact, and leaves the ERP behaviour real.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import app.nodes  # noqa: F401 - populates the node registry
from app.integrations.operations import ExternalOperationLedger
from app.mcp.d365_finance.handlers import HANDLERS
from app.mcp.d365_finance.tools import TOOL_DEFINITIONS
from app.mcp.dynamics.client import FixtureBackend
from app.mcp.registry import MCPServerConnection, MCPServerRegistry
from app.mcp.service import MCPIntegrationService
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow

FIXTURES_PATH = Path("app/mcp/d365_finance/fixtures.json")
SERVER_ID = "dynamics365_finance_scm"

LEVEL_1 = load_workflow("workflows/pump_routing_level_1.yaml")
LEVEL_2 = load_workflow("workflows/pump_routing_level_2.yaml")
LEVEL_3 = load_workflow("workflows/pump_manufacturer_case_routing.yaml")

# Accounts in the bundled fixtures, named so a test reads as a business case.
MERIDIAN = "Meridian Process Systems"      # key account, active owners in every role
BASF = "BASF SE"                           # key account, DACH owners
VANTAGE = "Vantage Fluid Handling"         # no account owner, territory owner only
BRISTOW = "Bristow Industrial"             # territory owner Jane Doe — INACTIVE
AMBIGUOUS = "BASF"                         # matches BASF SE and BASF Coatings GmbH


class _FakeTool:
    def __init__(self, definition: dict[str, Any]):
        self.name = definition["name"]
        self.title = None
        self.description = definition["description"]
        self.inputSchema = definition["input_schema"]
        self.outputSchema = definition["output_schema"]
        self.annotations = None
        self.meta = None


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResult:
    def __init__(self, *, structured=None, text: str = "", is_error: bool = False):
        self.structuredContent = structured
        self.content = [_FakeTextBlock(text)] if text else []
        self.isError = is_error


class D365FixtureClient:
    """Routes MCP calls to the real d365_finance handlers over the bundled
    fixtures. `failing_tools` makes a named tool raise, which is how the
    "the ERP did not answer" cases are exercised — the node's own
    `fail_on_error: false` then turns it into a routable status."""

    def __init__(self, failing_tools: tuple[str, ...] = ()):
        self.backend = FixtureBackend.from_file(FIXTURES_PATH)
        self.calls: list[dict[str, Any]] = []
        self.failing_tools = set(failing_tools)
        self.running_servers = (SERVER_ID,)

    async def list_tools(self, server):
        del server
        return [_FakeTool(definition) for definition in TOOL_DEFINITIONS]

    async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
        del server, timeout_seconds
        self.calls.append({"tool": name, "arguments": dict(arguments)})
        if name in self.failing_tools:
            raise ConnectionError(f"{name}: Dynamics 365 did not respond")
        payload = await HANDLERS[name](self.backend, arguments)
        return _FakeResult(structured=payload)

    def called(self, tool: str) -> bool:
        return any(call["tool"] == tool for call in self.calls)


def mcp_service(failing_tools: tuple[str, ...] = ()) -> MCPIntegrationService:
    registry = MCPServerRegistry()
    registry.add(MCPServerConnection(id=SERVER_ID, command="python", args=["-m", "stub"]))
    return MCPIntegrationService(
        registry=registry,
        client=D365FixtureClient(failing_tools),
        ledger=ExternalOperationLedger(),
    )


# --------------------------------------------------------------------------
# Scripted extractions — one baseline per level, overridden per case.
# --------------------------------------------------------------------------

LEVEL_1_EXTRACTION: dict[str, Any] = {
    "language": "en",
    "summary": "A test message.",
    "intent": "OTHER",
    "lifecycle_stage": "unknown",
    "product_or_part": "",
    "order_reference": "",
    "serial_number": "",
    "confidence": 0.95,
}

LEVEL_2_EXTRACTION: dict[str, Any] = {
    "language": "en",
    "summary": "A test message.",
    "intent": "OTHER",
    "lifecycle_stage": "unknown",
    "customer_name": "",
    "contact_name": "",
    "order_reference": "",
    "serial_number": "",
    "product_or_part": "",
    "technical_complexity": "standard",
    "requested_document_type": "NONE",
    "confidence": 0.95,
}

LEVEL_3_EXTRACTION: dict[str, Any] = {
    "language": "en",
    "english_summary": "A test message.",
    "primary_intent": "GENERAL_ENQUIRY",
    "secondary_intents": [],
    "secondary_reference": "",
    "requires_action": True,
    "customer_name": "",
    "contact_name": "",
    "quotation_reference": "",
    "customer_po_reference": "",
    "sales_order_reference": "",
    "invoice_reference": "",
    "pump_model": "",
    "serial_number": "",
    "requested_quantity": "",
    "requested_delivery_date": "",
    "lifecycle_stage": "unknown",
    "technical_complexity": "standard",
    "complaint_type": "none",
    "requested_document_type": "NONE",
    "price_or_terms_changed": False,
    "technical_change_requested": False,
    "order_reference_present": False,
    "serial_number_present": False,
    "confidence": 0.95,
    "missing_information": [],
}


class StubLLM:
    """Returns one scripted extraction. Each workflow makes exactly one model
    call, so no prompt-based dispatch is needed."""

    def __init__(self, baseline: dict[str, Any], overrides: dict[str, Any]):
        self.parsed = {**baseline, **overrides}
        self.calls: list[str] = []

    async def complete_structured(self, *, model, response_model, user=None, **_):
        self.calls.append(user or "")
        return response_model.model_validate_json(json.dumps(self.parsed))


async def _run(spec, baseline, extraction, run_id, inputs=None, failing_tools=()):
    service = mcp_service(failing_tools)
    result = await run_workflow(
        spec,
        inputs={"message": "Test message.", **(inputs or {})},
        services={"llm": StubLLM(baseline, extraction), "mcp": service},
        run_id=run_id,
    )
    result["mcp"] = service
    return result


async def run_level_1(extraction: dict[str, Any], run_id: str, **kwargs):
    return await _run(LEVEL_1, LEVEL_1_EXTRACTION, extraction, run_id, **kwargs)


async def run_level_2(extraction: dict[str, Any], run_id: str, **kwargs):
    return await _run(LEVEL_2, LEVEL_2_EXTRACTION, extraction, run_id, **kwargs)


async def run_level_3(extraction: dict[str, Any], run_id: str, **kwargs):
    return await _run(LEVEL_3, LEVEL_3_EXTRACTION, extraction, run_id, **kwargs)


# --------------------------------------------------------------------------
# Reading a finished run
# --------------------------------------------------------------------------


def outputs(result) -> dict[str, Any]:
    return result["state"]["node_outputs"]


def decisions(result, node_id: str) -> dict[str, Any]:
    return outputs(result)[node_id]["decisions"]


def routing(result) -> dict[str, Any]:
    """The Level 3 routing decision."""
    return decisions(result, "routing_decision")


def exit_packet(result, node_id: str) -> dict[str, Any]:
    return outputs(result)[node_id]["data"]


def reached(result, node_id: str) -> bool:
    return node_id in outputs(result)


def hitl_context(result) -> dict[str, Any]:
    interrupt = result["interrupt"]
    payload = interrupt[0].value if isinstance(interrupt, (list, tuple)) else interrupt
    return payload["context"]
