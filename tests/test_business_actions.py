"""Tests for typed Business View actions and their dispatch.

The point of the type union is that the set of things a business screen can
cause is enumerable and enforced server-side: an action outside it is refused,
an action owned by another endpoint is refused with the route that owns it, and
an action a person is not permitted is never offered in the first place
(§53, §54, §65).
"""
from __future__ import annotations

import pytest

from app.security.rbac import Role
from app.workflow.business_projection import build_business_projection
from app.workflow.business_view.actions import ActionContext, ActionFactory
from app.workflow.business_view.dispatch import (
    BusinessActionError,
    ClarificationDraft,
    dispatch_business_action,
)
from app.workflow.business_view.models import BusinessActionType
from app.workflow.business_view.runstate import build_run_view
from app.workflow.business_view.store import add_note, add_route_override
from tests.business_view_fixtures import BASF_COST_ENTRIES, PUMP_SPEC, basf_run


class _UpdateResult:
    def __init__(self, matched: int):
        self.matched_count = matched


class _Collection:
    def __init__(self, *, matches: bool = True):
        self._matches = matches
        self.updates: list[tuple[dict, dict]] = []

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update))
        return _UpdateResult(1 if self._matches else 0)

    async def find_one(self, *args, **kwargs):
        return None


class FakeDB:
    def __init__(self, *, matches: bool = True):
        self.collections: dict[str, _Collection] = {}
        self._matches = matches

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection(matches=self._matches))


class FakeLLM:
    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error

    async def complete_structured(self, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response


class FakeMCP:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


def factory(role: Role = Role.CONSULTANT, run=None) -> ActionFactory:
    view = build_run_view(run or basf_run(), workflow_spec=PUMP_SPEC)
    return ActionFactory(ActionContext(run=view, role=role))


async def dispatch(action_type: str, params=None, **kwargs):
    return await dispatch_business_action(
        action_type=action_type,
        params=params or {},
        run_id="run-basf-001",
        session_id="sess-1",
        username="maria",
        role="consultant",
        db=kwargs.pop("db", FakeDB()),
        services=kwargs.pop("services", {}),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The factory only offers what is valid
# ---------------------------------------------------------------------------


def test_state_decides_which_run_controls_exist():
    finished = factory()
    assert finished.pause() is None
    assert finished.stop() is None
    assert finished.recheck().params["mode"] == "restart"

    running = factory(run=basf_run(status="running", ended_at=None))
    assert running.pause() is not None
    assert running.stop() is not None
    assert running.recheck() is None

    failed = factory(run=basf_run(status="failed"))
    assert failed.recheck().params["mode"] == "retry"
    assert failed.recheck().label == "Retry safely"


def test_a_viewer_is_offered_no_mutating_action():
    viewer = factory(Role.VIEWER)

    assert viewer.pause() is None
    assert viewer.assign() is None
    assert viewer.add_note() is None
    assert viewer.route_override(current="Inside Sales") is None
    assert viewer.edit_fact("pump_model") is None
    assert viewer.draft_clarification(topic="pump_model") is None
    assert viewer.lookup_record(
        kind="order", reference="SO 1", tool="get_sales_order",
        server_id="dynamics365_finance_scm", argument="sales_order_reference",
    ) is None
    # Reading stays available.
    assert viewer.technical_details("handling") is not None
    assert viewer.explain() is not None


def test_approval_is_only_offered_when_a_gate_actually_allows_it():
    gate = {"paused": True, "pause_kind": "hitl_gate", "node_id": "n", "allowed_actions": ["reject"]}
    view = build_run_view(basf_run(status="paused"), workflow_spec=PUMP_SPEC, gate=gate)
    assert ActionFactory(ActionContext(run=view, role=Role.CONSULTANT)).approve() is None

    gate["allowed_actions"] = ["approve", "reject"]
    view = build_run_view(basf_run(status="paused"), workflow_spec=PUMP_SPEC, gate=gate)
    assert ActionFactory(ActionContext(run=view, role=Role.CONSULTANT)).approve() is not None


def test_every_offered_action_has_a_handler_or_a_named_owner():
    projection = build_business_projection(
        basf_run(), workflow_spec=PUMP_SPEC, cost_entries=BASF_COST_ENTRIES,
    )
    from app.workflow.business_view.dispatch import CLIENT_ACTIONS, DELEGATED_ACTIONS

    handled = {
        BusinessActionType.ADD_NOTE,
        BusinessActionType.ROUTE_OVERRIDE,
        BusinessActionType.DRAFT_CLARIFICATION,
        BusinessActionType.RELATED_RECORD_LOOKUP,
        BusinessActionType.EXPLAIN_DECISION,
    }
    offered = {
        action.type
        for group in (
            projection.allowed_actions,
            *[item.actions for item in projection.attention],
            *[activity.actions for activity in projection.activities],
        )
        for action in group
    }
    # No decorative buttons: every one is performed somewhere.
    assert offered <= handled | CLIENT_ACTIONS | set(DELEGATED_ACTIONS)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_action_type_is_refused_before_anything_runs():
    with pytest.raises(BusinessActionError, match="not a Business View action"):
        await dispatch("delete_everything")


@pytest.mark.asyncio
async def test_an_action_owned_by_another_endpoint_names_that_endpoint():
    with pytest.raises(BusinessActionError, match=r"/pause"):
        await dispatch("pause_run")
    with pytest.raises(BusinessActionError, match=r"fact-correction"):
        await dispatch("edit_fact", {"field": "pump_model"})


@pytest.mark.asyncio
async def test_a_client_side_action_is_refused_server_side():
    with pytest.raises(BusinessActionError, match="performed by the app"):
        await dispatch("open_technical_details", {"activity_id": "handling"})


@pytest.mark.asyncio
async def test_a_note_is_appended_to_the_run_with_its_author():
    db = FakeDB()
    result = await dispatch("add_note", {"text": "Called Klaus; datasheet on the way."}, db=db)

    assert result["kind"] == "note"
    assert result["note"]["by"] == "maria"
    _, update = db["run_history"].updates[0]
    assert update["$push"]["business_notes"]["text"].startswith("Called Klaus")


@pytest.mark.asyncio
async def test_an_empty_note_is_refused():
    with pytest.raises(BusinessActionError, match="needs some text"):
        await dispatch("add_note", {"text": "   "})


@pytest.mark.asyncio
async def test_a_route_override_records_who_decided_and_why():
    db = FakeDB()
    result = await dispatch(
        "route_override", {"route": "Technical Sales", "reason": "Needs engineering input"}, db=db,
    )

    assert result["override"]["route"] == "Technical Sales"
    assert result["override"]["by"] == "maria"
    _, update = db["run_history"].updates[0]
    assert update["$push"]["route_overrides"]["reason"] == "Needs engineering input"


@pytest.mark.asyncio
async def test_a_route_override_on_a_run_that_is_not_yours_is_a_lookup_error():
    with pytest.raises(LookupError):
        await dispatch("route_override", {"route": "Technical Sales"}, db=FakeDB(matches=False))


@pytest.mark.asyncio
async def test_a_clarification_is_drafted_and_explicitly_not_sent():
    llm = FakeLLM(ClarificationDraft(
        subject="Your quotation request",
        body="Could you confirm the pump model and required delivery date?",
        asks=["pump model", "delivery date"],
    ))

    result = await dispatch(
        "draft_clarification", {"topic": "pump_model"},
        services={"llm": llm},
        context={"customer": "BASF SE", "missing": ["Pump model"]},
    )

    assert result["kind"] == "clarification_draft"
    assert result["sent"] is False
    assert "Draft only" in result["note"]


@pytest.mark.asyncio
async def test_a_failed_draft_is_an_honest_error_not_an_empty_email():
    llm = FakeLLM(error=RuntimeError("provider down"))

    with pytest.raises(BusinessActionError) as excinfo:
        await dispatch("draft_clarification", {"topic": "pump_model"}, services={"llm": llm})

    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_a_record_lookup_calls_the_named_tool_with_the_named_argument():
    mcp = FakeMCP({"data": {"orders": [{"sales_order_reference": "SO 231706", "order_status": "CLOSED"}]}, "text": ""})

    result = await dispatch(
        "related_record_lookup",
        {"tool": "get_sales_order", "server_id": "dynamics365_finance_scm",
         "argument": "sales_order_reference", "reference": "SO 231706", "kind": "order"},
        services={"mcp": mcp},
    )

    assert mcp.calls[0]["tool_name"] == "get_sales_order"
    assert mcp.calls[0]["arguments"] == {"sales_order_reference": "SO 231706"}
    # A read never claims an approval it does not have.
    assert mcp.calls[0]["approval_satisfied"] is False
    assert result["reference"] == "SO 231706"


@pytest.mark.asyncio
async def test_a_lookup_against_an_unreachable_system_says_nothing_was_changed():
    mcp = FakeMCP(error=RuntimeError("connection refused"))

    with pytest.raises(BusinessActionError) as excinfo:
        await dispatch(
            "related_record_lookup",
            {"tool": "get_sales_order", "server_id": "d365", "argument": "ref", "reference": "SO 1"},
            services={"mcp": mcp},
        )

    assert "Nothing was changed" in str(excinfo.value)
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_a_lookup_with_no_connected_system_is_unavailable_not_broken():
    with pytest.raises(BusinessActionError) as excinfo:
        await dispatch(
            "related_record_lookup",
            {"tool": "get_sales_order", "server_id": "d365", "argument": "ref", "reference": "SO 1"},
            services={},
        )

    assert excinfo.value.status_code == 503


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_and_overrides_are_scoped_to_the_session():
    db = FakeDB()
    await add_note(db, run_id="r1", session_id="s1", text="hello", by="maria")
    await add_route_override(db, run_id="r1", session_id="s1", route="Service", reason=None, by="maria")

    for query, _ in db["run_history"].updates:
        assert query == {"run_id": "r1", "session_id": "s1"}


@pytest.mark.asyncio
async def test_a_note_is_length_capped():
    db = FakeDB()
    record = await add_note(db, run_id="r1", session_id="s1", text="x" * 5000, by="maria")

    assert len(record["text"]) == 2000
