"""Tests for app/workflow/business_projection.py.

Uses the real multilingual_customer_request_triage.yaml workflow spec (loaded
from disk, no mocking) with hand-built run documents shaped like what
run_history.py actually stores, so the stage-inference and decision-passthrough
logic is exercised against production experience metadata.
"""
from __future__ import annotations

from app.runtime.loader import load_workflow
from app.workflow.business_projection import build_business_projection, humanize_identifier

TRIAGE_SPEC = load_workflow("workflows/multilingual_customer_request_triage.yaml")


def _base_run_doc(**overrides):
    doc = {
        "run_id": "run-1",
        "session_id": "sess-1",
        "workflow_name": "Multilingual Customer Request Triage",
        "status": "running",
        "started_at": 1_700_000_000.0,
        "ended_at": None,
        "updated_at": 1_700_000_010.0,
        "node_types": {n.id: n.type for n in TRIAGE_SPEC.nodes},
        "node_runs": {},
        "outputs": {},
    }
    doc.update(overrides)
    return doc


def test_humanize_identifier_matches_frontend_behavior():
    assert humanize_identifier("understand_request") == "Understand request"
    assert humanize_identifier("") == "Workflow step"


def test_running_state_surfaces_active_node_as_current_activity():
    run_doc = _base_run_doc(
        node_runs={
            "incoming_request": {"status": "completed", "started_at": 1_700_000_000.0, "ended_at": 1_700_000_001.0},
            "understand_request": {"status": "running", "started_at": 1_700_000_001.0},
        },
        outputs={"incoming_request": {"data": {"message": "hello"}}},
    )
    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC)

    assert projection["current_activity"]["node_id"] == "understand_request"
    assert projection["current_activity"]["display_name"] == "Understand Customer Request"
    assert projection["current_activity"]["waiting_for_you"] is False
    assert projection["allowed_controls"] == ["pause", "stop"]
    assert projection["work_item"]["status"] == "In Progress"
    # incoming_request is inferred into the 'prepare' stage, understand_request into 'understand'.
    stage_ids = [stage["id"] for stage in projection["progress"]]
    assert "prepare" in stage_ids and "understand" in stage_ids


def test_paused_hitl_gate_surfaces_required_action_and_controls():
    run_doc = _base_run_doc(
        status="paused",
        node_runs={
            "understand_request": {
                "status": "completed", "started_at": 1_700_000_001.0, "ended_at": 1_700_000_005.0,
            },
            "automation_safety": {
                "status": "completed", "started_at": 1_700_000_005.0, "ended_at": 1_700_000_006.0,
            },
            "route_request": {"status": "paused", "started_at": 1_700_000_006.0, "ended_at": 1_700_000_006.0},
        },
        outputs={
            "understand_request": {
                "result": {
                    "intent": "spare_part_request",
                    "missing_information": ["product_model"],
                },
                "confidence": 0.62,
            },
            "automation_safety": {
                "decisions": {"human_review": True, "escalation_reason": "Low confidence."},
                "matched_rules": ["Low confidence needs a person"],
                "explanation": [
                    {"name": "Low confidence needs a person", "matched": True, "description": "Below 0.80 we do not act automatically."},
                    {"name": "Complaints are always handled by a person", "matched": False, "description": "n/a"},
                ],
                "summary": ["Escalated: low confidence"],
            },
        },
        node_types={n.id: n.type for n in TRIAGE_SPEC.nodes},
    )
    gate = {
        "paused": True,
        "pause_kind": "hitl_gate",
        "node_id": "human_review",
        "question": "Check the extracted information and decide how to proceed.",
        "allowed_actions": ["approve", "edit", "reject"],
    }

    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC, gate=gate)

    assert projection["required_user_actions"] == [{
        "type": "approval_review",
        "node_id": "human_review",
        "question": "Check the extracted information and decide how to proceed.",
        "allowed_actions": ["approve", "edit", "reject"],
    }]
    assert set(["approve", "edit", "reject", "ask_why", "stop"]).issubset(set(projection["allowed_controls"]))
    assert projection["missing_information"] == ["product_model"]
    assert projection["decision"]["decisions"]["human_review"] is True
    assert projection["decision"]["rules_triggered"] == ["Low confidence needs a person"]
    # Only the matched rule is exposed in the explanation, not the unmatched one.
    assert projection["decision_explanation"] == [{
        "name": "Low confidence needs a person",
        "description": "Below 0.80 we do not act automatically.",
    }]
    assert projection["understanding"]["result"]["intent"] == "spare_part_request"
    assert projection["understanding"]["confidence"] == 0.62


def test_completed_run_timeline_is_chronological_and_includes_final_status():
    run_doc = _base_run_doc(
        status="completed",
        ended_at=1_700_000_020.0,
        node_runs={
            "incoming_request": {"status": "completed", "started_at": 1_700_000_000.0, "ended_at": 1_700_000_001.0},
            "understand_request": {"status": "completed", "started_at": 1_700_000_001.0, "ended_at": 1_700_000_010.0},
        },
        outputs={"understand_request": {"result": {"intent": "quotation_request"}, "confidence": 0.95}},
    )
    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC)

    labels = [entry["label"] for entry in projection["timeline"]]
    assert labels[0] == "Request received"
    assert labels[-1] == "Completed"
    timestamps = [entry["ts"] for entry in projection["timeline"]]
    assert timestamps == sorted(timestamps)
    assert projection["work_item"]["status"] == "Completed"
    assert projection["allowed_controls"] == []


def test_user_requested_pause_offers_resume_not_approval():
    run_doc = _base_run_doc(status="paused")
    gate = {"paused": True, "pause_kind": "user_requested", "node_id": "understand_request"}

    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC, gate=gate)

    assert projection["required_user_actions"] == [{
        "type": "resume_decision",
        "node_id": "understand_request",
        "message": "This work is paused. Resume it when you're ready.",
    }]
    assert projection["allowed_controls"] == ["resume", "stop"]


def test_missing_workflow_spec_degrades_without_crashing():
    run_doc = _base_run_doc(
        node_runs={"understand_request": {"status": "running", "started_at": 1_700_000_001.0}},
        node_types={},
    )
    projection = build_business_projection(run_doc, workflow_spec=None)

    assert projection["work_item"]["id"] == "run-1"
    assert projection["progress"] == []
    assert projection["current_activity"] is None


def test_failed_run_offers_retry():
    run_doc = _base_run_doc(status="failed", ended_at=1_700_000_030.0)
    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC)

    assert projection["allowed_controls"] == ["retry", "stop"]
    assert projection["work_item"]["status"] == "Needs Attention"


def test_assigned_to_passes_through_to_the_work_item():
    run_doc = _base_run_doc(assigned_to="Maria")
    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC)

    assert projection["work_item"]["assigned_to"] == "Maria"


def test_assigned_to_is_none_when_never_set():
    run_doc = _base_run_doc()
    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC)

    assert projection["work_item"]["assigned_to"] is None


def test_editable_facts_are_empty_for_a_workflow_fact_corrections_does_not_know():
    # multilingual_customer_request_triage.yaml's understand_request output
    # has no fields in app.workflow.fact_corrections.EDITABLE_FIELDS.
    run_doc = _base_run_doc(
        node_runs={"understand_request": {"status": "completed", "started_at": 1_700_000_001.0, "ended_at": 1_700_000_005.0}},
        outputs={"understand_request": {"result": {"intent": "quotation_request"}, "confidence": 0.95}},
    )
    projection = build_business_projection(run_doc, workflow_spec=TRIAGE_SPEC)

    assert projection["editable_facts"] == []
    assert projection["stale_decisions"] == []


def test_editable_facts_and_stale_decisions_for_the_flagship_workflow():
    from app.runtime.loader import load_workflow

    flagship_spec = load_workflow("workflows/crm_aware_customer_triage.yaml")
    run_doc = {
        "run_id": "run-2",
        "session_id": "sess-1",
        "workflow_name": "CRM-Aware Customer Triage",
        "status": "paused",
        "started_at": 1_700_000_000.0,
        "ended_at": None,
        "updated_at": 1_700_000_010.0,
        "node_types": {n.id: n.type for n in flagship_spec.nodes},
        "node_runs": {
            "understand_request": {"status": "completed", "started_at": 1_700_000_001.0, "ended_at": 1_700_000_005.0},
        },
        "outputs": {
            "understand_request": {
                "result": {"request_types": ["technical_support"], "hazardous_area": False, "pressure": None},
                "confidence": 0.9,
            },
        },
        "stale_decisions": ["complexity", "human_review"],
    }

    projection = build_business_projection(run_doc, workflow_spec=flagship_spec)

    assert projection["editable_facts"] == ["hazardous_area", "pressure", "request_types"]
    assert projection["stale_decisions"] == ["complexity", "human_review"]
