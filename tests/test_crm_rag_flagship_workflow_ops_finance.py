"""Rigorous, synthetic Operations-Head / Finance test scenarios for the
flagship triage workflow (workflows/crm_aware_customer_triage.yaml).

Complements tests/test_crm_rag_flagship_workflow.py, which covers the four
Verder demo cases (standard/technical/complex/ambiguous). This file exists to
close specific gaps in `assess_request`'s rule coverage that those four cases
never exercise: order status, complaints, multi-account and multi-order CRM
ambiguity, the "no model + no history" spare-part path, open-opportunity
flagging, low extraction confidence, and priority between simultaneously
matched rules. Each test is framed as a message an Operations Head or a
Finance person at a customer company might plausibly send.

Reuses the exact same fixture-backed MCP harness and StubLLM pattern as
test_crm_rag_flagship_workflow.py — the fixtures in
app/mcp/dynamics/fixtures.json already contain the ambiguous data these tests
need (two "ABC Chemicals" accounts, one account with two orders and one with
one, an account with an open opportunity and one with none).
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
    async def __call__(self, q, llm=None):
        from app.retrieval.models import RetrievalResult, RetrievedChunk

        return RetrievalResult(
            query=q.query, rewritten_query=None,
            chunks=[RetrievedChunk(
                chunk_id="manual-001", doc_id="manual-001",
                doc_title="Verderflex Dura Series Service Manual", doc_type="manual",
                text="General maintenance guidance.", metadata={}, hybrid_score=0.9,
            )],
            filters_applied=q.filters, timings_ms={"total_ms": 5.0},
        )


BASE_EXTRACTION = {
    "language": "en",
    "request_summary": "Synthetic ops/finance test case.",
    "organization": "Nordvand Process AS",
    "requestor": {"name": None, "email": None, "phone": None},
    "product_model": None,
    "serial_number": None,
    "quantity": None,
    "refers_to_previous_purchase": False,
    "production_stopped": False,
    "urgency": "normal",
    "requested_action": "See request.",
    "application": None,
    "medium": None,
    "flow_rate": None,
    "pressure": None,
    "temperature": None,
    "viscosity": None,
    "hazardous_area": False,
    "requires_custom_design": False,
    "asks_for_price": False,
    "asks_for_availability": False,
    "asks_for_technical_advice": False,
    "missing_information": [],
    "blocking_missing_information": [],
    "ambiguities": [],
    "suggested_actions": [],
}


async def _run(extraction, message, subject="Request", sender_email="ops@customer.example", confidence=0.9):
    llm = StubLLM(extraction, confidence=confidence)
    retriever = StubRetriever()
    mcp = _mcp_service()
    result = await run_workflow(
        WORKFLOW,
        inputs={"subject": subject, "message": message, "sender_email": sender_email},
        services={"llm": llm, "retriever": retriever, "mcp": mcp},
    )
    return result, retriever, mcp


def _decisions(result):
    return result["state"]["node_outputs"]["assess_request"]["decisions"]


@pytest.mark.asyncio
async def test_ops_order_status_inquiry_routes_to_sales():
    """Ops Head: 'where is our order, the line is waiting on it.'"""
    extraction = {**BASE_EXTRACTION, "request_types": ["order_status"], "requested_action": "Confirm delivery date for SO-2024-XXXX."}
    result, _retriever, _mcp = await _run(
        extraction, "Where is our order? Our production line is waiting on this delivery.",
    )
    decisions = _decisions(result)

    assert result["status"] == "completed"
    assert decisions["primary_intent"] == "order_status"
    assert decisions["human_review"] is False
    assert result["state"]["node_outputs"]["route_request"]["route"] == "sales"


@pytest.mark.asyncio
async def test_ops_complaint_about_a_faulty_pump_always_reaches_a_person():
    """Ops Head: 'the pump you sent leaks constantly, this is unacceptable.'"""
    extraction = {**BASE_EXTRACTION, "organization": "ABC Chemicals B.V.", "request_types": ["complaint"]}
    result, _retriever, _mcp = await _run(
        extraction, "The pump you delivered leaks constantly. This is unacceptable and needs to be fixed.",
    )
    decisions = _decisions(result)

    assert result["status"] == "paused"
    assert decisions["primary_intent"] == "complaint"
    assert decisions["human_review"] is True
    assert "reviewed by a person" in decisions["escalation_reason"]
    assert result["state"]["node_outputs"]["route_request"]["route"] == "human_review"


@pytest.mark.asyncio
async def test_ambiguous_company_name_matching_two_accounts_needs_a_person():
    """Finance: 'ABC Chemicals' names no legal suffix — B.V. and GmbH both exist."""
    extraction = {**BASE_EXTRACTION, "organization": "ABC Chemicals", "request_types": ["quotation_request"]}
    result, _retriever, mcp = await _run(extraction, "Please send us a quotation for two pumps.")
    decisions = _decisions(result)

    assert result["status"] == "paused"
    assert decisions["human_review"] is True
    assert "more than one crm account" in decisions["escalation_reason"].lower()
    find_account_calls = [c for c in mcp.client.calls if c["tool"] == "find_account"]
    assert len(find_account_calls) == 1


@pytest.mark.asyncio
async def test_vague_reference_with_two_past_orders_lets_a_person_choose():
    """Finance: 'send us another one of the usual' — ABC Chemicals GmbH has two
    past orders on file, so the system must not guess which one."""
    extraction = {
        **BASE_EXTRACTION, "organization": "ABC Chemicals GmbH", "request_types": ["spare_part_request"],
        "product_model": None, "refers_to_previous_purchase": True,
        "ambiguities": ["Customer referred to 'the usual' order without naming a product."],
        "missing_information": ["product_model"], "blocking_missing_information": ["product_model"],
    }
    result, _retriever, _mcp = await _run(extraction, "Please send us another one of the usual.")
    decisions = _decisions(result)

    assert result["status"] == "paused"
    assert decisions["product_resolved"] is False
    assert decisions["human_review"] is True
    assert "more than one past order" in decisions["escalation_reason"]


@pytest.mark.asyncio
async def test_spare_part_request_with_no_model_and_no_order_history():
    """Ops Head at a customer with NO order history at all asks for 'the spare
    part' without naming it or referencing a past purchase — a distinct rule
    path from the 'vague previous-purchase reference' cases above."""
    extraction = {
        **BASE_EXTRACTION, "organization": "Nordvand Process AS", "request_types": ["spare_part_request"],
        "product_model": None, "refers_to_previous_purchase": False,
        "missing_information": ["product_model"], "blocking_missing_information": ["product_model"],
    }
    result, _retriever, _mcp = await _run(extraction, "We need the spare part for our pump, please advise.")
    decisions = _decisions(result)

    assert result["status"] == "paused"
    assert decisions["product_resolved"] is False
    assert decisions["human_review"] is True
    assert decisions["escalation_reason"] == "A spare part cannot be identified without a product model."


@pytest.mark.asyncio
async def test_known_customer_with_an_open_opportunity_is_flagged_but_not_escalated():
    """Finance: a routine quote request from a customer Sales is already
    talking to — has_open_opportunity should be true, but that alone must not
    force human review."""
    extraction = {**BASE_EXTRACTION, "organization": "ABC Chemicals GmbH", "request_types": ["quotation_request"], "asks_for_price": True}
    result, _retriever, _mcp = await _run(extraction, "Could you send us a quotation for two replacement pumps?")
    decisions = _decisions(result)

    assert result["status"] == "completed"
    assert decisions["has_open_opportunity"] is True
    assert decisions["human_review"] is False
    assert result["state"]["node_outputs"]["route_request"]["route"] == "sales"


@pytest.mark.asyncio
async def test_customer_with_no_opportunities_is_not_flagged():
    """Contrast case for the above — Nordvand has no opportunities on file."""
    extraction = {**BASE_EXTRACTION, "organization": "Nordvand Process AS", "request_types": ["quotation_request"], "asks_for_price": True}
    result, _retriever, _mcp = await _run(extraction, "Please quote us for a new pump.")
    decisions = _decisions(result)

    assert decisions["has_open_opportunity"] is False
    assert decisions["human_review"] is False


@pytest.mark.asyncio
async def test_complaint_takes_priority_over_a_simultaneous_quotation_request():
    """A message that is BOTH a complaint and a quote request must be treated
    as a complaint — primary_intent rules run in ascending business priority,
    so the later (complaint) rule must win over quotation_request."""
    extraction = {
        **BASE_EXTRACTION, "organization": "ABC Chemicals B.V.",
        "request_types": ["quotation_request", "complaint"], "asks_for_price": True,
    }
    result, _retriever, _mcp = await _run(
        extraction, "The last pump you sold us is defective, and we also need a quote for two more.",
    )
    decisions = _decisions(result)

    assert decisions["primary_intent"] == "complaint"
    assert decisions["human_review"] is True
    assert result["state"]["node_outputs"]["route_request"]["route"] == "human_review"


@pytest.mark.asyncio
async def test_low_extraction_confidence_needs_a_person_even_with_clean_fields():
    """Finance: message is short/unclear enough that the model itself is not
    confident, even though every field it did extract looks well-formed."""
    extraction = {**BASE_EXTRACTION, "organization": "ABC Chemicals B.V.", "request_types": ["quotation_request"]}
    result, _retriever, _mcp = await _run(extraction, "quote pls, 2 units", confidence=0.55)
    decisions = _decisions(result)

    assert result["status"] == "paused"
    assert decisions["human_review"] is True
    assert "0.80 threshold" in decisions["escalation_reason"]


@pytest.mark.asyncio
async def test_hazardous_area_alone_without_custom_design_is_still_complex():
    """Isolates the ATEX/hazardous-area rule from requires_custom_design —
    the existing complex-tier test sets both flags at once and never proves
    hazardous_area alone is sufficient."""
    extraction = {
        **BASE_EXTRACTION, "organization": "ABC Chemicals B.V.", "request_types": ["technical_support"],
        "hazardous_area": True, "requires_custom_design": False, "asks_for_technical_advice": True,
    }
    result, _retriever, _mcp = await _run(
        extraction, "We need a pump for a classified ATEX zone, standard duty otherwise.",
    )
    decisions = _decisions(result)

    assert decisions["complexity"] == "complex"
    assert decisions["human_review"] is True
    assert "specialist" in decisions["escalation_reason"].lower()


@pytest.mark.asyncio
async def test_production_stopped_sets_critical_urgency_without_forcing_escalation():
    """Ops Head: production is down, but it's an ordinary quote request with no
    other red flag — urgency must reflect the business impact, but urgency
    alone must not trigger human review (that would defeat the point of
    automating the routine case)."""
    extraction = {
        **BASE_EXTRACTION, "organization": "ABC Chemicals GmbH", "request_types": ["quotation_request"],
        "production_stopped": True, "asks_for_price": True,
    }
    result, _retriever, _mcp = await _run(
        extraction, "Our line is down and we need a replacement pump quoted urgently.",
    )
    decisions = _decisions(result)

    assert result["status"] == "completed"
    assert decisions["urgency"] == "critical"
    assert decisions["human_review"] is False


@pytest.mark.asyncio
async def test_availability_check_with_no_price_ask_still_routes_to_sales():
    """Finance/procurement: asking purely about availability/lead time, not
    price — asks_for_availability should be true, asks_for_price false, and
    it should still be a routine sales-routed case."""
    extraction = {
        **BASE_EXTRACTION, "organization": "ABC Chemicals B.V.", "request_types": ["quotation_request"],
        "asks_for_price": False, "asks_for_availability": True,
    }
    result, _retriever, _mcp = await _run(
        extraction, "How soon could two Dura 25 pumps ship if we ordered today? We do not need pricing yet.",
    )
    decisions = _decisions(result)

    assert result["status"] == "completed"
    assert decisions["human_review"] is False
    assert result["state"]["node_outputs"]["route_request"]["route"] == "sales"
