"""Golden-case model-comparison eval for the flagship triage workflow.

Mirrors the service stubbing in tests/test_crm_rag_flagship_workflow.py (real
rules engine + real MCP dynamics fixture handlers, only the LLM/retriever
transport is faked) so these tests prove the *eval runner's* scoring and
aggregation logic, not the workflow's business logic (already covered there).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.nodes  # noqa: F401
from app.evaluation.workflow_golden import (
    ModelOverrideGateway,
    load_workflow_golden_set,
    recommend_model,
    run_golden_set_with_model,
    run_workflow_golden_case,
)
from app.integrations.operations import ExternalOperationLedger
from app.mcp.dynamics.client import FixtureBackend
from app.mcp.dynamics.handlers import HANDLERS
from app.mcp.dynamics.tools import TOOL_DEFINITIONS
from app.mcp.registry import MCPServerConnection, MCPServerRegistry
from app.mcp.service import MCPIntegrationService
from app.observability.cost_ledger import CostLedger

GOLDEN_PATH = Path("eval/golden_set/verder_customer_triage.json")


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
        self.backend = FixtureBackend.from_file(Path("app/mcp/dynamics/fixtures.json"))
        self.running_servers = ("dynamics365",)

    async def list_tools(self, server):
        del server
        return [
            _FakeTool(d["name"], description=d["description"], input_schema=d["input_schema"], output_schema=d["output_schema"])
            for d in TOOL_DEFINITIONS
        ]

    async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
        del server, timeout_seconds
        payload = await HANDLERS[name](self.backend, arguments)
        return _FakeResult(structured=payload)


def _mcp_service() -> MCPIntegrationService:
    registry = MCPServerRegistry()
    registry.add(MCPServerConnection(id="dynamics365", command="python", args=["-m", "whatever"]))
    return MCPIntegrationService(registry=registry, client=DynamicsFixtureClient(), ledger=ExternalOperationLedger())


class _StubCompletion:
    def __init__(self, text: str):
        self.text = text
        self.model = "stub"
        self.input_tokens = 10
        self.output_tokens = 5


class StubLLM:
    """Returns a canned extraction regardless of message text — these tests
    check the eval runner's scoring, not the model's real extraction
    ability. Records every model it was actually called with, so the tests
    can prove ModelOverrideGateway genuinely overrides AUTO_MODEL routing."""

    def __init__(self, extraction: dict, rag_answer: str = "Stub grounded answer [1]."):
        self._extraction = extraction
        self._rag_answer = rag_answer
        self.received_models: list[str] = []

    def with_context(self, **_kwargs):
        return self

    async def complete_structured(self, *, model, response_model, **_):
        self.received_models.append(model)
        payload = dict(self._extraction)
        payload.setdefault("confidence", 0.9)
        return response_model.model_validate_json(json.dumps(payload))

    async def complete(self, *, model, system=None, user=None, **_):
        self.received_models.append(model)
        return _StubCompletion(self._rag_answer)


BASE_EXTRACTION = {
    "language": "de",
    "request_summary": "Golden-case stub extraction.",
    "organization": "ABC Chemicals GmbH",
    "requestor": {"name": None, "email": None, "phone": None},
    "product_model": "Dura 35",
    "serial_number": None,
    "quantity": 2,
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
    "asks_for_price": True,
    "asks_for_availability": False,
    "asks_for_technical_advice": False,
    "missing_information": [],
    "blocking_missing_information": [],
    "ambiguities": [],
    "suggested_actions": [],
}

# Extraction payloads that, fed through the REAL rules engine, are known (from
# tests/test_crm_rag_flagship_workflow.py) to reach exactly the outcome each
# golden case in eval/golden_set/verder_customer_triage.json expects.
EXTRACTION_BY_CASE = {
    "standard": {**BASE_EXTRACTION, "request_types": ["quotation_request"]},
    "technical": {
        **BASE_EXTRACTION,
        "request_types": ["technical_support", "quotation_request"],
        "missing_information": ["operating_pressure"],
        "asks_for_technical_advice": True,
    },
    "complex": {
        **BASE_EXTRACTION,
        "request_types": ["technical_support"],
        "urgency": "high",
        "hazardous_area": True,
        "requires_custom_design": True,
    },
    "ambiguous": {
        **BASE_EXTRACTION,
        "request_types": ["spare_part_request"],
        "product_model": None,
        "refers_to_previous_purchase": True,
        "ambiguities": ["Customer asked for 'the same pump as last time' without naming it."],
        "missing_information": ["product_model"],
        "blocking_missing_information": ["product_model"],
    },
}


def _services(extraction: dict) -> dict:
    return {
        "llm": StubLLM(extraction),
        "retriever": _StubRetriever(),
        "mcp": _mcp_service(),
        "cost_ledger": CostLedger(None),
    }


class _StubRetriever:
    async def __call__(self, q, llm=None):
        from app.retrieval.models import RetrievalResult, RetrievedChunk

        return RetrievalResult(
            query=q.query, rewritten_query=None,
            chunks=[RetrievedChunk(
                chunk_id="manual-001", doc_id="manual-001",
                doc_title="Verderflex Dura Series Service Manual", doc_type="manual",
                text="Pressure swings on a Dura 35 usually indicate worn hose or a clogged suction line.",
                metadata={}, hybrid_score=0.9,
            )],
            filters_applied=q.filters, timings_ms={"total_ms": 5.0},
        )


def test_loads_the_four_verder_demo_cases():
    cases = load_workflow_golden_set(GOLDEN_PATH)
    assert [c.id for c in cases] == ["standard", "technical", "complex", "ambiguous"]
    assert all(c.expected for c in cases)


@pytest.mark.asyncio
async def test_a_correct_extraction_passes_every_expected_field():
    cases = {c.id: c for c in load_workflow_golden_set(GOLDEN_PATH)}
    case = cases["technical"]
    services = _services(EXTRACTION_BY_CASE["technical"])

    result = await run_workflow_golden_case(case, model="claude-haiku-4-5", services=services, run_id="test:technical")

    assert result.passed, [c.model_dump() for c in result.checks if not c.passed]
    assert result.error is None
    assert {c.field for c in result.checks} == set(case.expected)


@pytest.mark.asyncio
async def test_model_override_reaches_the_llm_even_though_the_yaml_says_auto():
    cases = {c.id: c for c in load_workflow_golden_set(GOLDEN_PATH)}
    case = cases["standard"]
    services = _services(EXTRACTION_BY_CASE["standard"])

    await run_workflow_golden_case(case, model="claude-haiku-4-5", services=services, run_id="test:standard")

    stub = services["llm"]
    assert stub.received_models, "the stub should have been called at least once"
    assert set(stub.received_models) == {"claude-haiku-4-5"}, "auto must never reach the LLM under the override"


@pytest.mark.asyncio
async def test_a_wrong_extraction_fails_with_a_diagnostic_mismatch():
    cases = {c.id: c for c in load_workflow_golden_set(GOLDEN_PATH)}
    case = cases["complex"]
    # A model that misses the ATEX/custom-design signal entirely — never
    # reaches "complex" or a human, which is exactly the failure mode this
    # golden case exists to catch.
    wrong_extraction = {**EXTRACTION_BY_CASE["complex"], "hazardous_area": False, "requires_custom_design": False}
    services = _services(wrong_extraction)

    result = await run_workflow_golden_case(case, model="gpt-4o-mini", services=services, run_id="test:complex-wrong")

    assert not result.passed
    complexity_check = next(c for c in result.checks if c.field == "complexity")
    assert complexity_check.expected == "complex"
    assert complexity_check.actual != "complex"
    assert not complexity_check.passed


@pytest.mark.asyncio
async def test_run_golden_set_aggregates_pass_rate_cost_and_latency():
    cases = load_workflow_golden_set(GOLDEN_PATH)

    async def fake_run(case, *, model, services, run_id):
        del services, run_id
        return await run_workflow_golden_case(
            case, model=model,
            services=_services(EXTRACTION_BY_CASE[case.id]),
            run_id=f"agg:{model}:{case.id}",
        )

    import app.evaluation.workflow_golden as wg
    original = wg.run_workflow_golden_case
    wg.run_workflow_golden_case = fake_run
    try:
        comparison = await run_golden_set_with_model(
            cases, model="claude-haiku-4-5", services={}, run_id_prefix="agg",
        )
    finally:
        wg.run_workflow_golden_case = original

    assert comparison.total_cases == 4
    assert comparison.passed_cases == 4
    assert comparison.pass_rate == 1.0
    assert comparison.avg_cost_usd is not None
    assert comparison.avg_latency_ms is not None


def test_recommend_model_prefers_higher_pass_rate_then_lower_cost():
    from app.evaluation.workflow_golden import ModelComparisonResult

    good = ModelComparisonResult(
        model="model-a", total_cases=4, passed_cases=4, pass_rate=1.0,
        avg_cost_usd=0.01, avg_latency_ms=500.0, cases=[],
    )
    cheaper_but_worse = ModelComparisonResult(
        model="model-b", total_cases=4, passed_cases=2, pass_rate=0.5,
        avg_cost_usd=0.001, avg_latency_ms=200.0, cases=[],
    )
    all_failed = ModelComparisonResult(
        model="model-c", total_cases=4, passed_cases=0, pass_rate=0.0,
        avg_cost_usd=0.0005, avg_latency_ms=100.0, cases=[],
    )

    rec = recommend_model([good, cheaper_but_worse, all_failed])
    assert rec["model"] == "model-a"

    assert recommend_model([all_failed]) is None


@pytest.mark.asyncio
async def test_model_override_gateway_forwards_the_pinned_model():
    stub = StubLLM(BASE_EXTRACTION)
    gateway = ModelOverrideGateway(stub, "claude-haiku-4-5")

    scoped = gateway.with_context(run_id="x")  # should still be an override gateway
    await scoped.complete(model="auto", system="s", user="u")

    assert stub.received_models == ["claude-haiku-4-5"]
