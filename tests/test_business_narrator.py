"""Tests for the BusinessStatusNarrator and the decision explainer.

Both exist to turn already-decided state into better English. Both must be
impossible to depend on: the screen renders identically when the model is
absent, slow, or wrong, and anything it says that is not supported by the
input it was given is discarded rather than shown (§15, §16, §62, §63).
"""
from __future__ import annotations

import pytest

from app.workflow.business_projection import build_business_projection
from app.workflow.business_view import explanation as explanation_module
from app.workflow.business_view import narrator
from tests.business_view_fixtures import BASF_COST_ENTRIES, PUMP_SPEC, basf_run


def projection():
    return build_business_projection(
        basf_run(), workflow_spec=PUMP_SPEC, cost_entries=BASF_COST_ENTRIES,
    )


class FakeLLM:
    """Records what it was asked and returns whatever the test wants."""

    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls: list[dict] = []

    async def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


def test_the_narrator_only_ever_sees_bounded_structured_state():
    source = narrator.narration_input(projection())

    assert source.business_status == "Ready for Inside Sales"
    assert source.business_decision == "Inside Sales"
    assert "Customer: BASF SE" in source.important_facts
    assert "Pump model" in source.missing_information
    assert "Request understood" in source.completed_actions
    # No raw payload, no prompts, no node ids reach the model.
    encoded = source.model_dump_json()
    assert "understand_message" not in encoded
    assert "parsed" not in encoded


@pytest.mark.asyncio
async def test_a_valid_narration_is_used_and_reported_as_ai():
    llm = FakeLLM(narrator.Narration(
        headline="Ready for Inside Sales review",
        summary="BASF is requesting a quotation for five pumps and spare parts from an earlier order.",
        next_step="Inside Sales reviews the request before preparing the quotation.",
    ))

    result, source, model = await narrator.narrate(llm, projection())

    assert source == "ai"
    assert result.headline == "Ready for Inside Sales review"
    assert model == narrator.NARRATION_MODEL
    # Bounded and cheap by construction (§15, §51).
    assert llm.calls[0]["max_tokens"] <= 256
    assert llm.calls[0]["temperature"] <= 0.2


@pytest.mark.asyncio
async def test_an_invented_fact_is_rejected_in_favour_of_the_template():
    llm = FakeLLM(narrator.Narration(
        headline="Ready for Inside Sales",
        # Neither the delivery date nor Siemens is anywhere in the input.
        summary="BASF needs five pumps delivered by 14 March, alongside the Siemens order.",
        next_step="",
    ))
    projected = projection()

    result, source, model = await narrator.narrate(llm, projected)

    assert source == "deterministic"
    assert model is None
    assert result.headline == projected.business_status.headline
    assert result.summary == projected.business_status.summary


@pytest.mark.asyncio
async def test_an_over_long_narration_is_rejected():
    llm = FakeLLM(narrator.Narration(
        headline="Ready for Inside Sales " * 6, summary="Ready for Inside Sales.", next_step="",
    ))

    _, source, _ = await narrator.narrate(llm, projection())

    assert source == "deterministic"


@pytest.mark.asyncio
async def test_an_unavailable_model_falls_back_silently():
    llm = FakeLLM(error=RuntimeError("provider unavailable"))
    projected = projection()

    result, source, _ = await narrator.narrate(llm, projected)

    assert source == "deterministic"
    assert result.headline == projected.business_status.headline


@pytest.mark.asyncio
async def test_no_gateway_at_all_still_produces_a_narration():
    projected = projection()

    result, source, model = await narrator.narrate(None, projected)

    assert (source, model) == ("deterministic", None)
    assert result.summary == projected.business_status.summary


def test_applying_a_narration_changes_only_the_wording():
    projected = projection()
    code, tone, count = (
        projected.business_status.code,
        projected.business_status.tone,
        projected.business_status.attention_count,
    )

    narrator.apply(
        projected,
        narrator.Narration(headline="Ready for Inside Sales review", summary="Rewritten.", next_step="Review it."),
        source="ai",
        model="gpt-5.6-luna",
    )

    assert projected.business_status.headline == "Ready for Inside Sales review"
    assert projected.business_status.narration_source == "ai"
    assert projected.business_status.narration_model == "gpt-5.6-luna"
    # The authoritative parts are untouched — a narrator may not move state.
    assert (projected.business_status.code, projected.business_status.tone) == (code, tone)
    assert projected.business_status.attention_count == count


def test_the_same_state_produces_the_same_cache_key():
    first, second = projection(), projection()

    assert first.business_status.state_version == second.business_status.state_version


# ---------------------------------------------------------------------------
# Explanation
# ---------------------------------------------------------------------------


def decision():
    return projection().decision


@pytest.mark.asyncio
async def test_the_deterministic_explanation_lists_only_the_runs_own_evidence():
    view = await explanation_module.explain(None, decision())

    assert view.source == "deterministic"
    assert view.decision == "Inside Sales"
    labels = {fact["label"] for fact in view.facts}
    assert {"Customer state", "Order state", "Primary department"} <= labels
    assert any("Primary department → Sales" == rule["name"] for rule in view.rules)


@pytest.mark.asyncio
async def test_a_well_cited_explanation_is_used():
    view = explanation_module.deterministic_explanation(decision())
    llm = FakeLLM(explanation_module.DecisionExplanation(
        summary=(
            "The request went to Inside Sales because it is a standard quotation "
            "request with no safety issue or production stoppage."
        ),
        fact_refs=[view.facts[0]["id"]],
        rule_refs=[view.rules[0]["id"]],
    ))

    result = await explanation_module.explain(llm, decision())

    assert result.source == "ai"
    assert "Inside Sales" in result.summary
    assert result.model == explanation_module.EXPLANATION_MODEL


@pytest.mark.asyncio
async def test_an_explanation_citing_something_that_does_not_exist_is_discarded():
    llm = FakeLLM(explanation_module.DecisionExplanation(
        summary="Routed to Inside Sales because the customer has a preferred-partner agreement.",
        fact_refs=["check:preferred_partner"],
        rule_refs=[],
    ))

    result = await explanation_module.explain(llm, decision())

    assert result.source == "deterministic"
    assert result.summary != "Routed to Inside Sales because the customer has a preferred-partner agreement."


@pytest.mark.asyncio
async def test_an_explanation_citing_nothing_at_all_is_discarded():
    llm = FakeLLM(explanation_module.DecisionExplanation(
        summary="It seemed like the right team.", fact_refs=[], rule_refs=[],
    ))

    result = await explanation_module.explain(llm, decision())

    assert result.source == "deterministic"


@pytest.mark.asyncio
async def test_an_explanation_failure_keeps_the_evidence_on_screen():
    llm = FakeLLM(error=RuntimeError("provider unavailable"))

    result = await explanation_module.explain(llm, decision())

    assert result.source == "deterministic"
    assert result.facts and result.rules
