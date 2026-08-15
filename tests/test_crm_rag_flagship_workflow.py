"""End-to-end regression test for the flagship demo: multilingual
understanding + CRM lookup + conditional RAG + deterministic decision + HITL,
combined in workflows/crm_aware_customer_triage.yaml (the §7/§54 scenario).

Runs the real workflow with only the LLM, retriever, and MCP transport
stubbed — the rules engine, router, MCP policy dispatch, and the real
Dynamics fixture-backed handlers (same ones `app/mcp/dynamics/server.py`
serves over stdio) all execute for real. No subprocess is spawned; only the
stdio transport is replaced, exactly as tests/test_mcp_tool_node.py does for
its own fake server.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.nodes  # noqa: F401
from app.integrations.operations import ExternalOperationLedger
from app.mcp.dynamics.client import FixtureBackend
from app.mcp.dynamics.handlers import HANDLERS
from app.mcp.dynamics.tools import TOOL_DEFINITIONS
from app.mcp.registry import MCPServerConnection, MCPServerRegistry
from app.mcp.service import MCPIntegrationService
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow

WORKFLOW = load_workflow("workflows/crm_aware_customer_triage.yaml")
FIXTURES_PATH = Path("app/mcp/dynamics/fixtures.json")


class _FakeTool:
    def __init__(self, name, *, description="", input_schema=None, output_schema=None):
        self.name = name
        self.title = None
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}
        self.outputSchema = output_schema
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


class DynamicsFixtureClient:
    """Routes MCP calls to the real Dynamics handlers against the bundled
    fixtures — the same business logic and data app/mcp/dynamics/server.py
    serves, minus the stdio subprocess."""

    def __init__(self):
        self.backend = FixtureBackend.from_file(FIXTURES_PATH)
        self.calls: list[dict] = []
        self.running_servers = ("dynamics365",)

    async def list_tools(self, server):
        del server
        return [
            _FakeTool(
                d["name"], description=d["description"],
                input_schema=d["input_schema"], output_schema=d["output_schema"],
            )
            for d in TOOL_DEFINITIONS
        ]

    async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
        self.calls.append({"tool": name, "arguments": dict(arguments), "server": server})
        payload = await HANDLERS[name](self.backend, arguments)
        return _FakeResult(structured=payload)


def _mcp_service() -> MCPIntegrationService:
    registry = MCPServerRegistry()
    registry.add(MCPServerConnection(id="dynamics365", command="python", args=["-m", "whatever"]))
    return MCPIntegrationService(
        registry=registry, client=DynamicsFixtureClient(), ledger=ExternalOperationLedger()
    )


class _StubCompletion:
    def __init__(self, text: str):
        self.text = text
        self.model = "stub"
        self.input_tokens = 10
        self.output_tokens = 5


class StubLLM:
    """Handles both calls this workflow makes: the extraction
    (complete_structured) and the RAG node's grounded generation (complete)."""

    def __init__(self, extraction: dict, confidence: float = 0.9, rag_answer: str = "Stub grounded answer [1]."):
        self._extraction = extraction
        self._confidence = confidence
        self._rag_answer = rag_answer

    async def complete_structured(self, *, model, response_model, **_):
        payload = dict(self._extraction)
        payload.setdefault("confidence", self._confidence)
        return response_model.model_validate_json(json.dumps(payload))

    async def complete(self, *, model, system=None, user=None, **_):
        return _StubCompletion(self._rag_answer)


class StubRetriever:
    """RAGAgent's own retriever call, not the real hybrid-search pipeline.
    Records whether it was ever invoked, so tests can prove the conditional
    branch genuinely skips retrieval rather than just hiding its result."""

    def __init__(self):
        self.call_count = 0

    async def __call__(self, q, llm=None):
        from app.retrieval.models import RetrievalResult, RetrievedChunk

        self.call_count += 1
        chunks = [
            RetrievedChunk(
                chunk_id="manual-001",
                doc_id="manual-001",
                doc_title="Verderflex Dura Series Service Manual",
                doc_type="manual",
                text="Pressure swings on a Dura 35 usually indicate worn hose or a clogged suction line.",
                metadata={},
                hybrid_score=0.9,
            ),
        ]
        return RetrievalResult(
            query=q.query,
            rewritten_query=None,
            chunks=chunks,
            filters_applied=q.filters,
            timings_ms={"total_ms": 5.0},
        )


GERMAN_PUMP_EXTRACTION = {
    "language": "de",
    "request_types": ["technical_support", "quotation_request"],
    "request_summary": "Production down due to pressure swings on a Verderflex Dura 35; also wants a quote for two replacement pumps.",
    "organization": "ABC Chemicals GmbH",
    "requestor": {"name": None, "email": None, "phone": None},
    "product_model": "Dura 35",
    "serial_number": None,
    "quantity": 2,
    "refers_to_previous_purchase": False,
    "production_stopped": True,
    "urgency": "critical",
    "requested_action": "Fix the pressure swings urgently and quote two replacement pumps.",
    "application": None,
    "medium": None,
    "flow_rate": None,
    "pressure": None,
    "temperature": None,
    "viscosity": None,
    "hazardous_area": False,
    "requires_custom_design": False,
    "asks_for_price": True,
    "asks_for_availability": False,
    "asks_for_technical_advice": True,
    "missing_information": ["operating_pressure"],
    "blocking_missing_information": [],
    "ambiguities": [],
    "suggested_actions": ["Create an urgent technical support case", "Start a quotation for two replacement pumps"],
}


async def _run(extraction, message, subject="Request", sender_email="customer@abc-chemicals.example"):
    llm = StubLLM(extraction)
    retriever = StubRetriever()
    mcp = _mcp_service()
    result = await run_workflow(
        WORKFLOW,
        inputs={"subject": subject, "message": message, "sender_email": sender_email},
        services={"llm": llm, "retriever": retriever, "mcp": mcp},
    )
    return result, retriever, mcp


@pytest.mark.asyncio
async def test_technical_request_runs_rag_finds_crm_and_reaches_support():
    result, retriever, mcp = await _run(
        GERMAN_PUMP_EXTRACTION,
        "Unsere Produktionslinie steht. Verderflex Dura 35, starke Druckschwankungen.",
    )

    assert result["status"] == "completed"
    assert retriever.call_count == 1, "RAG must run for a technical request"
    assert any(call["tool"] == "find_account" for call in mcp.client.calls)

    decisions = result["state"]["node_outputs"]["assess_request"]["decisions"]
    assert decisions["primary_intent"] == "technical_support"
    assert decisions["customer_known"] is True  # ABC Chemicals GmbH is in the bundled fixtures

    assert result["state"]["node_outputs"]["route_request"]["route"] == "technical_support"
    assert "product_knowledge_support" in result["state"]["node_outputs"]
    assert "product_knowledge_parts" not in result["state"]["node_outputs"]
    handoff = result["state"]["node_outputs"]["technical_support"]["data"]
    assert handoff["model"] == "Dura 35"
    assert "Stub grounded answer" in handoff["technical_answer"]
    assert handoff["detected_intents"] == ["technical_support", "quotation_request"]


@pytest.mark.asyncio
async def test_pure_quotation_request_skips_rag_entirely():
    """§27/§29: RAG is not spent on a request with no technical content."""
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["quotation_request"]
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"
    extraction["missing_information"] = []
    extraction["blocking_missing_information"] = []

    result, retriever, _mcp = await _run(extraction, "Bitte senden Sie uns ein Angebot fuer zwei Pumpen.")

    assert result["status"] == "completed"
    assert retriever.call_count == 0, "a pure quotation request must never trigger retrieval"
    assert "product_knowledge" not in result["state"]["node_outputs"]
    assert result["state"]["node_outputs"]["route_request"]["route"] == "sales"


@pytest.mark.asyncio
async def test_unknown_company_escalates_to_human_review():
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["organization"] = "Totally Unknown Company That Has Never Ordered Anything Ltd"
    extraction["request_types"] = ["quotation_request"]
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"

    result, _retriever, _mcp = await _run(extraction, "Bitte senden Sie uns ein Angebot.")

    assert result["status"] == "paused"
    decisions = result["state"]["node_outputs"]["assess_request"]["decisions"]
    assert decisions["human_review"] is True
    assert decisions["customer_known"] is False
    assert "not in the CRM" in decisions["escalation_reason"]


@pytest.mark.asyncio
async def test_a_message_with_no_subject_or_sender_still_runs():
    """Both are declared `required: false` and both are mapped into the AI
    step's context. Pasting only a message body — the ordinary case from the
    Builder — used to end the run before any work was done with a raw
    `AITaskConfig context.Subject: Input should be a valid string` error."""
    result, _retriever, _mcp = await _run(
        GERMAN_PUMP_EXTRACTION,
        "Unsere Produktionslinie steht. Verderflex Dura 35.",
        subject=None,
        sender_email=None,
    )

    assert result["status"] == "completed"
    assert result["state"]["node_outputs"]["route_request"]["route"] == "technical_support"


@pytest.mark.asyncio
async def test_vague_previous_purchase_reference_is_never_guessed():
    """§13/§127: the ambiguous-reference demo — the system must resolve it
    from real order history or send it to a person, never guess."""
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["spare_part_request"]
    extraction["product_model"] = None
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"
    extraction["refers_to_previous_purchase"] = True
    extraction["ambiguities"] = ["Customer asked for 'the same pump as last time' without naming it."]
    extraction["missing_information"] = ["product_model"]
    extraction["blocking_missing_information"] = ["product_model"]

    result, _retriever, _mcp = await _run(
        extraction, "Bitte schicken Sie uns wieder die gleiche Pumpe wie letztes Mal.",
    )

    assert result["status"] == "paused"
    decisions = result["state"]["node_outputs"]["assess_request"]["decisions"]
    assert decisions["human_review"] is True
    assert decisions["product_resolved"] is False


@pytest.mark.asyncio
async def test_atex_requirement_forces_specialist_review():
    """§6 Complex demo case: ATEX + multiple corrosive fluids + variable
    pressure must never be auto-routed — it always reaches a specialist."""
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["technical_support"]
    extraction["production_stopped"] = False
    extraction["urgency"] = "high"
    extraction["hazardous_area"] = True
    extraction["requires_custom_design"] = True
    extraction["missing_information"] = []
    extraction["blocking_missing_information"] = []

    result, _retriever, _mcp = await _run(
        extraction,
        "Wir benoetigen eine Sonderanlage fuer mehrere aggressive Fluessigkeiten "
        "mit ATEX-Anforderung und variablem Betriebsdruck.",
    )

    assert result["status"] == "paused"
    decisions = result["state"]["node_outputs"]["assess_request"]["decisions"]
    assert decisions["complexity"] == "complex"
    assert decisions["human_review"] is True
    assert "specialist" in decisions["escalation_reason"].lower()
    # The complex signal must win even though this would otherwise have
    # auto-routed to Sales Engineering — no product_knowledge_support run,
    # since routing never reached that branch.
    assert "product_knowledge_support" not in result["state"]["node_outputs"]


@pytest.mark.asyncio
async def test_three_simultaneous_parameters_are_complex_without_atex():
    """The multi-constraint complex signal fires even with no ATEX flag."""
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["technical_support"]
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"
    extraction["pressure"] = "6 bar"
    extraction["temperature"] = "90°C"
    extraction["viscosity"] = "high"
    extraction["missing_information"] = []
    extraction["blocking_missing_information"] = []

    result, _retriever, _mcp = await _run(
        extraction, "Wir brauchen eine Pumpe fuer 6 bar, 90 Grad und hohe Viskositaet.",
    )

    decisions = result["state"]["node_outputs"]["assess_request"]["decisions"]
    assert decisions["complexity"] == "complex"
    assert decisions["human_review"] is True


@pytest.mark.asyncio
async def test_plain_technical_question_is_technical_not_complex():
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["technical_support"]
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"
    extraction["hazardous_area"] = False
    extraction["requires_custom_design"] = False
    extraction["pressure"] = None
    extraction["temperature"] = "60°C"
    extraction["viscosity"] = None
    extraction["missing_information"] = []
    extraction["blocking_missing_information"] = []

    result, _retriever, _mcp = await _run(
        extraction, "Welche Pumpe empfehlen Sie fuer 60 Grad?",
    )

    assert result["status"] == "completed"
    decisions = result["state"]["node_outputs"]["assess_request"]["decisions"]
    assert decisions["complexity"] == "technical"
    assert decisions["human_review"] is False
    assert result["state"]["node_outputs"]["route_request"]["route"] == "technical_support"
