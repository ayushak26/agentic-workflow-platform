"""Tests for app/workflow/fact_corrections.py — editing an extracted fact on
a run and marking the decisions derived from it as stale."""
from __future__ import annotations

import pytest

from app.workflow.fact_corrections import (
    EDITABLE_FIELDS,
    FACT_DEPENDENCIES,
    apply_fact_correction,
    stale_decisions_for,
)


class _FakeUpdateResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count


class _FakeRunHistoryCollection:
    """Records the update it was asked to perform; doesn't need real Mongo
    semantics since apply_fact_correction only ever issues one update_one
    call per correction."""

    def __init__(self, *, matches: bool = True):
        self._matches = matches
        self.calls: list[tuple[dict, dict]] = []

    async def update_one(self, query, update):
        self.calls.append((query, update))
        return _FakeUpdateResult(matched_count=1 if self._matches else 0)


class _FakeDB:
    def __init__(self, *, matches: bool = True):
        self.run_history = _FakeRunHistoryCollection(matches=matches)

    def __getitem__(self, name):
        assert name == "run_history"
        return self.run_history


def test_editable_fields_matches_dependency_map_keys():
    assert EDITABLE_FIELDS == frozenset(FACT_DEPENDENCIES)


def test_stale_decisions_for_a_known_field():
    assert stale_decisions_for("hazardous_area") == ("complexity", "human_review", "escalation_reason")


def test_stale_decisions_for_an_unknown_field_is_empty():
    assert stale_decisions_for("not_a_real_field") == ()


@pytest.mark.asyncio
async def test_apply_fact_correction_sets_the_field_and_marks_dependents_stale():
    db = _FakeDB()

    edit = await apply_fact_correction(
        db, run_id="run-1", session_id="sess-1", field="pressure", value="6 bar",
    )

    assert edit["field"] == "pressure"
    assert edit["value"] == "6 bar"
    assert set(edit["stale_decisions"]) == {"complexity", "human_review", "escalation_reason"}

    [(query, update)] = db.run_history.calls
    assert query == {"run_id": "run-1", "session_id": "sess-1"}
    assert update["$set"]["outputs.understand_request.result.pressure"] == "6 bar"
    assert update["$push"]["fact_edits"]["field"] == "pressure"
    assert set(update["$addToSet"]["stale_decisions"]["$each"]) == {"complexity", "human_review", "escalation_reason"}


@pytest.mark.asyncio
async def test_apply_fact_correction_rejects_a_field_not_in_the_map():
    db = _FakeDB()
    with pytest.raises(ValueError):
        await apply_fact_correction(db, run_id="run-1", session_id="sess-1", field="not_a_real_field", value="x")
    assert db.run_history.calls == []


@pytest.mark.asyncio
async def test_apply_fact_correction_raises_when_the_run_does_not_belong_to_this_session():
    db = _FakeDB(matches=False)
    with pytest.raises(LookupError):
        await apply_fact_correction(db, run_id="run-1", session_id="sess-1", field="pressure", value="6 bar")


# ---------------------------------------------------------------------------
# Dependencies derived from a workflow's own routers, for any workflow whose
# branching is expressed that way (Business View's generalised path).
# ---------------------------------------------------------------------------


def test_dependencies_are_derived_from_the_workflows_own_router_conditions():
    from app.runtime.loader import load_workflow
    from app.workflow.fact_corrections import derive_dependencies

    spec = load_workflow("workflows/pump_manufacturer_case_routing.yaml")
    dependencies = derive_dependencies(spec, "understand_message")

    # Nothing hand-maintained: these come from the rules' own conditions.
    assert "routing_decision" in dependencies["primary_intent"]
    assert "routing_decision" in dependencies["lifecycle_stage"]
    assert "routing_decision" in dependencies["technical_complexity"]
    # A field no rule reads has no dependents, so nothing is marked stale for it.
    assert "english_summary" not in dependencies


def test_derived_dependencies_ignore_other_nodes_paths():
    from app.runtime.loader import load_workflow
    from app.workflow.fact_corrections import derive_dependencies

    spec = load_workflow("workflows/pump_manufacturer_case_routing.yaml")
    dependencies = derive_dependencies(spec, "find_customer")

    # `find_customer.first.key_account` is read by a router; the extraction
    # node's fields are not attributed to it.
    assert "primary_intent" not in dependencies
    assert {"count", "found", "status"} <= set(dependencies)


def test_derive_dependencies_on_a_missing_spec_claims_no_staleness():
    from app.workflow.fact_corrections import derive_dependencies

    assert derive_dependencies(None, "understand_message") == {}


@pytest.mark.asyncio
async def test_a_correction_can_target_another_workflows_extraction_node():
    from app.workflow.fact_corrections import apply_fact_correction as apply

    db = _FakeDB()
    edit = await apply(
        db,
        run_id="run-9",
        session_id="sess-1",
        field="pump_model",
        value="P-100",
        node_id="understand_message",
        payload_key="parsed",
        stale_decisions=("order_availability_router",),
        allowed_fields={"pump_model"},
    )

    [(_, update)] = db.run_history.calls
    assert update["$set"]["outputs.understand_message.parsed.pump_model"] == "P-100"
    assert edit["node_id"] == "understand_message"
    assert update["$addToSet"]["stale_decisions"]["$each"] == ["order_availability_router"]


@pytest.mark.asyncio
async def test_a_field_this_run_does_not_expose_is_rejected():
    from app.workflow.fact_corrections import apply_fact_correction as apply

    db = _FakeDB()
    with pytest.raises(ValueError):
        await apply(
            db, run_id="run-9", session_id="sess-1", field="not_a_field", value="x",
            node_id="understand_message", payload_key="parsed", allowed_fields={"pump_model"},
        )
    assert db.run_history.calls == []


@pytest.mark.asyncio
async def test_a_field_no_rule_reads_is_still_editable_and_marks_nothing_stale():
    from app.workflow.fact_corrections import apply_fact_correction as apply

    db = _FakeDB()
    await apply(
        db, run_id="run-9", session_id="sess-1", field="requested_delivery_date", value="2026-03-14",
        node_id="understand_message", payload_key="parsed",
        stale_decisions=(), allowed_fields={"requested_delivery_date"},
    )

    [(_, update)] = db.run_history.calls
    # A correction with no dependents must not fabricate one.
    assert "$addToSet" not in update
