"""Regression test for the reworked BusinessRequestUnderstanding-style schema
in multilingual_customer_request_triage.yaml (Priority 1, §8-9): multi-intent
request_types, blocking vs. non-blocking missing information, ambiguities,
and the deterministic primary_intent derivation that routing depends on.

Runs the real workflow end-to-end with only the LLM stubbed — no mocking of
the rules engine, router, or executor.
"""
from __future__ import annotations

import json

import pytest

import app.nodes  # noqa: F401
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow

WORKFLOW = load_workflow("workflows/multilingual_customer_request_triage.yaml")


class StubLLM:
    """Returns one scripted extraction regardless of prompt content — this
    workflow has exactly one AI node, so no routing-by-prompt is needed."""

    def __init__(self, extraction: dict, confidence: float = 0.9):
        self._extraction = extraction
        self._confidence = confidence
        self.calls: list[dict] = []

    async def complete_structured(self, *, model, response_model, **_):
        self.calls.append({"model": model})
        payload = dict(self._extraction)
        payload.setdefault("confidence", self._confidence)
        return response_model.model_validate_json(json.dumps(payload))


GERMAN_PUMP_EXTRACTION = {
    "language": "de",
    "request_types": ["technical_support", "quotation_request"],
    "request_summary": "Production line down due to pressure swings on a Verderflex Dura 35; also wants a quote for two replacement pumps.",
    "requestor": {"name": None, "email": None, "phone": None},
    "organization": None,
    "product": "peristaltic pump",
    "product_model": "Dura 35",
    "serial_number": None,
    "quantity": 2,
    "medium": None,
    "flow_rate": None,
    "production_stopped": True,
    "urgency": "critical",
    "requested_action": "Fix the pressure swings urgently and quote two replacement pumps.",
    "missing_information": ["operating_pressure", "serial_number"],
    "blocking_missing_information": [],
    "ambiguities": [],
    "suggested_actions": [
        "Create an urgent technical support case",
        "Start a quotation for two replacement Dura 35 pumps",
    ],
}


@pytest.mark.asyncio
async def test_multi_intent_request_routes_on_higher_priority_intent():
    """Technical support outranks quotation_request when both are present —
    the production-down case must not get silently routed to Sales."""
    llm = StubLLM(GERMAN_PUMP_EXTRACTION)
    result = await run_workflow(
        WORKFLOW,
        inputs={
            "subject": "Dringend: Produktionslinie steht",
            "message": "Unsere Produktionslinie steht seit heute Morgen...",
        },
        services={"llm": llm},
    )

    assert result["status"] == "completed"
    understood = result["state"]["node_outputs"]["understand_request"]["result"]
    assert understood["request_types"] == ["technical_support", "quotation_request"]
    assert understood["product_model"] == "Dura 35"
    assert understood["quantity"] == 2

    decisions = result["state"]["node_outputs"]["automation_safety"]["decisions"]
    assert decisions["primary_intent"] == "technical_support"
    assert decisions["urgency"] == "critical"
    assert decisions["human_review"] is False

    router_output = result["state"]["node_outputs"]["route_request"]
    assert router_output["route"] == "technical_support"

    handoff = result["state"]["node_outputs"]["technical_support"]["data"]
    assert handoff["model"] == "Dura 35"
    assert handoff["detected_intents"] == ["technical_support", "quotation_request"]
    assert handoff["urgency"] == "critical"


@pytest.mark.asyncio
async def test_blocking_missing_information_forces_human_review_for_spare_parts():
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["spare_part_request"]
    extraction["product_model"] = None
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"
    extraction["missing_information"] = ["product_model"]
    extraction["blocking_missing_information"] = ["product_model"]

    llm = StubLLM(extraction)
    result = await run_workflow(
        WORKFLOW,
        inputs={"subject": "Ersatzteil", "message": "Wir brauchen ein Ersatzteil für unsere Pumpe."},
        services={"llm": llm},
    )

    assert result["status"] == "paused"
    interrupt = result["state"]["__interrupt__"][0].value
    assert interrupt["node_id"] == "human_review"
    decisions = result["state"]["node_outputs"]["automation_safety"]["decisions"]
    assert decisions["clarification_required"] is True
    assert decisions["human_review"] is True
    assert decisions["escalation_reason"] == "A spare part cannot be identified without the product model."


@pytest.mark.asyncio
async def test_ambiguous_and_incomplete_request_escalates_rather_than_guessing():
    """§13/§127: an ambiguous reference combined with a real information gap
    must never be resolved by a plausible guess — it goes to a person."""
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["spare_part_request"]
    extraction["product_model"] = None
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"
    extraction["ambiguities"] = ["Customer asked for 'another one of the pumps we bought last year' without naming which one."]
    extraction["missing_information"] = ["product_model"]
    extraction["blocking_missing_information"] = ["product_model"]

    llm = StubLLM(extraction)
    result = await run_workflow(
        WORKFLOW,
        inputs={"subject": "Nachbestellung", "message": "Bitte schicken Sie uns wieder eine der Pumpen von letztem Jahr."},
        services={"llm": llm},
    )

    assert result["status"] == "paused"
    decisions = result["state"]["node_outputs"]["automation_safety"]["decisions"]
    assert decisions["human_review"] is True
    assert "ambiguous" in decisions["escalation_reason"].lower()


@pytest.mark.asyncio
async def test_single_intent_quotation_request_routes_to_sales():
    extraction = dict(GERMAN_PUMP_EXTRACTION)
    extraction["request_types"] = ["quotation_request"]
    extraction["production_stopped"] = False
    extraction["urgency"] = "normal"
    extraction["missing_information"] = []
    extraction["blocking_missing_information"] = []

    llm = StubLLM(extraction)
    result = await run_workflow(
        WORKFLOW,
        inputs={"subject": "Angebot", "message": "Bitte senden Sie uns ein Angebot für zwei Pumpen."},
        services={"llm": llm},
    )

    assert result["status"] == "completed"
    assert result["state"]["node_outputs"]["route_request"]["route"] == "sales"
    assert result["state"]["node_outputs"]["automation_safety"]["decisions"]["primary_intent"] == "quotation_request"
