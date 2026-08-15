"""Tests for the Business View projection (app/workflow/business_view/).

These are written against the real pump-routing and triage workflow specs
loaded from disk, with run documents shaped exactly as run_history.py stores
them — so what is asserted here is what a business user actually sees.

The central claims under test are the ones the redesign turns on:

* fourteen technical nodes become four or five *business activities*, and
  start/completed pairs never reach the timeline (§8, §43, §59);
* raw model JSON is not in the payload at all (§60);
* status is derived from workflow state, never from the latest event (§61);
* a missing field arrives with the actions that could resolve it (§66);
* an AI result names the model that executed, and a rule-based one carries no
  model at all (§64, §25).
"""
from __future__ import annotations

import json

from app.runtime.loader import load_workflow
from app.security.rbac import Role
from app.workflow.business_projection import build_business_projection, humanize_identifier
from tests.business_view_fixtures import (
    BASF_COST_ENTRIES,
    BASF_PARSED,
    PUMP_SPEC,
    T0,
    basf_run,
    node_run,
)

TRIAGE_SPEC = load_workflow("workflows/multilingual_customer_request_triage.yaml")


def project(run=None, **kwargs):
    return build_business_projection(
        run if run is not None else basf_run(),
        workflow_spec=kwargs.pop("workflow_spec", PUMP_SPEC),
        cost_entries=kwargs.pop("cost_entries", BASF_COST_ENTRIES),
        **kwargs,
    )


def find_activity(projection, activity_id):
    return next((a for a in projection.activities if a.id == activity_id), None)


def fact_display(facts, label):
    return next((f.display for f in facts if f.label == label), None)


# ---------------------------------------------------------------------------
# §59 / §43 — event grouping
# ---------------------------------------------------------------------------


def test_router_nodes_collapse_into_one_handling_activity():
    projection = project()

    handling = find_activity(projection, "handling")
    assert handling is not None
    assert handling.title == "Handling checks completed"
    # Five decision steps answered "how should this be handled" — one activity.
    assert len(handling.source_nodes) == 5
    assert {"routing_decision", "primary_department_router"} <= set(handling.source_nodes)


def test_thirteen_technical_nodes_become_five_business_activities():
    projection = project()

    assert [a.id for a in projection.activities] == [
        "understand", "enrich", "handling", "ownership", "outcome",
    ]
    assert projection.activity_summary == {
        "completed": 5, "total": 5, "technical_nodes": 13,
    }


def test_timeline_never_contains_started_or_completed_node_events():
    projection = project()

    titles = [entry.title for entry in projection.timeline]
    assert not any(title.endswith(" started") for title in titles)
    assert not any("router" in title.lower() for title in titles)
    assert "Request received" in titles
    assert "Handling checks completed" in titles
    # Twenty-six start/complete events became a readable handful.
    assert len(projection.timeline) <= 9


def test_handling_timeline_entry_carries_its_checks_as_marks():
    projection = project()

    entry = next(e for e in projection.timeline if e.title == "Handling checks completed")
    assert "Primary department: SALES" in entry.marks
    assert "Count: 1" in entry.marks


def test_timeline_is_chronological():
    projection = project()

    timestamps = [entry.ts for entry in projection.timeline]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# §60 — raw JSON is not in the payload
# ---------------------------------------------------------------------------


def test_raw_model_output_is_absent_from_the_whole_projection():
    projection = project()
    payload = projection.model_dump_json()

    # The extraction node's `raw` field is a JSON string of every parsed value.
    # None of it may reach the business surface.
    assert "\\\"has_safety_issue\\\"" not in payload
    assert '"raw"' not in payload
    assert "english_summary" not in payload
    # The summary itself is present — as a sentence, not as a JSON field.
    assert BASF_PARSED["english_summary"] in projection.business_status.summary


def test_understanding_exposes_business_fields_not_the_payload():
    projection = project()

    labels = [field.label for field in projection.understanding.fields]
    assert "Customer" in labels
    assert "Request" in labels
    # Machinery is not a business fact.
    assert "Raw" not in labels
    assert "Confidence" not in labels
    assert "Missing information" not in labels
    assert fact_display(projection.understanding.fields, "Quantity") == "5"
    assert fact_display(projection.understanding.fields, "Lifecycle") == "Presales"
    assert projection.understanding.confidence == 0.86


def test_nested_objects_are_dropped_rather_than_flattened_into_json():
    run = basf_run()
    run["outputs"]["understand_message"]["parsed"]["diagnostics"] = {"tokens": 900}
    projection = project(run)

    assert "Diagnostics" not in [field.label for field in projection.understanding.fields]
    assert "tokens" not in projection.model_dump_json()


# ---------------------------------------------------------------------------
# §12 / §13 / §61 — deterministic status
# ---------------------------------------------------------------------------


def test_completed_run_with_a_handling_team_is_ready_for_that_team():
    projection = project()

    assert projection.business_status.code == "ready_for_team"
    assert projection.business_status.headline == "Ready for Inside Sales"
    assert projection.business_status.tone == "done"
    assert projection.business_status.attention_count == 5


def test_running_status_names_the_activity_in_progress_not_the_last_event():
    run = basf_run(status="running", ended_at=None)
    run["node_runs"] = {
        "understand_message": node_run("completed", T0, T0 + 4),
        "find_customer": node_run("running", T0 + 4),
    }
    run["outputs"] = {"understand_message": run["outputs"]["understand_message"]}
    projection = project(run)

    assert projection.business_status.code == "in_progress"
    assert projection.business_status.headline == "Checking business systems"


def test_paused_at_a_gate_is_waiting_for_approval():
    run = basf_run(status="paused", ended_at=None)
    gate = {
        "paused": True, "pause_kind": "hitl_gate", "node_id": "customer_ambiguous",
        "question": "Confirm which account this case belongs to.",
        "allowed_actions": ["approve", "reject"],
    }
    projection = project(run, gate=gate)

    assert projection.business_status.code == "waiting_for_approval"
    assert projection.business_status.headline == "Waiting for approval"
    assert projection.required_user_actions[0].type == "approval_review"
    assert projection.next_step.blocked is True


def test_user_requested_pause_is_paused_not_waiting_for_approval():
    run = basf_run(status="paused", ended_at=None)
    gate = {"paused": True, "pause_kind": "user_requested", "node_id": "find_customer"}
    projection = project(run, gate=gate)

    assert projection.business_status.code == "paused"
    assert projection.required_user_actions[0].type == "resume_decision"
    assert "resume_run" in {action.type.value for action in projection.allowed_actions}


def test_failed_run_needs_attention_and_says_nothing_was_changed():
    run = basf_run(status="failed")
    run["node_runs"]["get_sales_order"] = node_run(
        "failed", T0 + 6, T0 + 7, error="Dynamics 365 did not respond",
    )
    projection = project(run)

    assert projection.business_status.code == "failed"
    assert projection.business_status.headline == "Needs attention"
    assert "Nothing was changed" in projection.business_status.summary


def test_rejected_run_is_stopped():
    projection = project(basf_run(status="rejected"))

    assert projection.business_status.code == "stopped"


def test_completed_run_without_a_team_but_with_gaps_waits_for_information():
    run = basf_run()
    # No terminal handoff note, no routers and no rule decisions: nothing
    # states where it went.
    del run["outputs"]["sales_queue"]
    for node_id in list(run["outputs"]):
        if node_id.endswith("_router") or node_id.endswith("_decision") or node_id == "business_facts":
            del run["outputs"][node_id]
    run["node_runs"] = {"understand_message": run["node_runs"]["understand_message"]}
    projection = project(run)

    assert projection.decision is None
    assert projection.business_status.code == "needs_information"


def test_state_version_ignores_timing_but_tracks_business_change():
    baseline = project().business_status.state_version

    slower = basf_run()
    slower["node_runs"]["understand_message"]["ended_at"] = T0 + 40
    slower["ended_at"] = T0 + 60
    assert project(slower).business_status.state_version == baseline

    changed = basf_run()
    changed["outputs"]["understand_message"]["parsed"]["requested_quantity"] = "6"
    assert project(changed).business_status.state_version != baseline


# ---------------------------------------------------------------------------
# §6 / §7 / §66 — attention and its resolution actions
# ---------------------------------------------------------------------------


def test_missing_pump_model_offers_the_related_order_and_manual_entry():
    projection = project()

    item = next(i for i in projection.attention if i.title == "Pump model")
    labels = [action.label for action in item.actions]
    assert "Open SO 231706" in labels          # the reference the customer gave
    assert "Get order details" in labels        # the ERP tool this workflow uses
    assert "Enter manually" in labels
    assert "Ask customer" in labels


def test_missing_delivery_date_offers_manual_entry_and_asking_the_customer():
    projection = project()

    item = next(i for i in projection.attention if i.title == "Requested delivery date")
    labels = [action.label for action in item.actions]
    assert labels == ["Enter manually", "Ask customer"]


def test_a_datasheet_gap_offers_the_document_when_one_is_actually_attached():
    run = basf_run()
    run["inputs"]["datasheet"] = {
        "kind": "workflow_file", "file_id": "wf_1", "name": "Technical Datasheet.pdf",
        "extension": ".pdf", "category": "document", "content_type": "application/pdf",
        "size_bytes": 120_000, "sha256": "0" * 64, "minio_key": "sess/wf_1.pdf",
        "parseable_text": True,
    }
    projection = project(run)

    item = next(i for i in projection.attention if i.title == "Technical specifications")
    assert item.actions[0].label == "Review datasheet"
    assert item.status_label == "May be in Technical Datasheet.pdf"
    assert projection.attachments[0].name == "Technical Datasheet.pdf"


def test_no_document_action_is_offered_when_nothing_is_actually_attached():
    projection = project()

    item = next(i for i in projection.attention if i.title == "Technical specifications")
    # The customer's prose mentions a datasheet; the run has no file. Offering
    # to open one would be a lie.
    assert [a.type.value for a in item.actions] == ["draft_clarification"]
    assert projection.attachments == []


def test_a_gap_a_rule_reads_outranks_one_no_rule_reads():
    projection = project()

    severities = {item.title: item.severity for item in projection.attention}
    assert severities["Pump model"] == "warning"
    assert severities["Technical specifications"] == "info"
    assert projection.attention[0].title == "Pump model"


def test_a_missing_item_the_run_later_filled_in_is_not_reported():
    run = basf_run()
    run["outputs"]["understand_message"]["parsed"]["contact_name"] = "Klaus Brenner"
    projection = project(run)

    assert "Contact" not in [item.title for item in projection.attention]


def test_a_system_that_did_not_answer_becomes_a_warning_with_a_retry():
    run = basf_run(status="failed")
    run["node_runs"]["get_order"] = node_run("failed", T0 + 6, T0 + 7, error="timeout")
    run["outputs"]["get_order"] = {"status": "error", "found": False, "count": 0, "error": "timeout"}
    projection = project(run)

    item = next(i for i in projection.attention if i.title.startswith("Could not check"))
    assert item.severity == "warning"
    assert "Retry" in [action.label for action in item.actions]
    assert "No changes were made" in (item.detail or "") or "timeout" in (item.detail or "")


def test_several_matching_customer_accounts_are_flagged_as_needing_a_choice():
    run = basf_run()
    run["outputs"]["find_customer"]["count"] = 3
    projection = project(run)

    item = next(i for i in projection.attention if "accounts match" in i.title)
    assert item.status_label == "Needs a choice"


# ---------------------------------------------------------------------------
# §19 / §20 — decision and its evidence
# ---------------------------------------------------------------------------


def test_decision_names_the_team_with_its_reason_and_supporting_facts():
    projection = project()

    decision = projection.decision
    assert decision.headline == "Inside Sales"
    assert decision.summary == "New equipment enquiry"
    assert "have not yet bought" in decision.reason
    supporting = {fact.label: fact.display for fact in decision.facts}
    assert supporting["Primary department"] == "SALES"
    assert supporting["Customer state"] == "FOUND"
    assert supporting["Order state"] == "FOUND"
    assert supporting["Serial state"] == "ABSENT"


def test_decision_rules_quote_the_routers_that_fired():
    projection = project()

    names = [rule.name for rule in projection.decision.rules]
    assert "Primary department → Sales" in names
    assert "Additional requests → Single" in names


def test_a_router_reports_its_subject_rather_than_its_branch_name():
    projection = project()

    handling = find_activity(projection, "handling")
    # `primary_department_router` routed on a field, not on a branch name:
    # "Primary department: SALES" is the fact a reader wants.
    assert fact_display(handling.facts, "Primary department") == "SALES"
    assert "Single" not in [fact.display for fact in handling.facts]


def test_a_human_route_override_is_shown_as_the_persons_decision():
    run = basf_run()
    run["route_overrides"] = [{
        "route": "Technical Sales", "reason": "Needs engineering input",
        "by": "maria", "at": "2026-08-14T18:00:00+00:00",
    }]
    projection = project(run)

    assert projection.decision.headline == "Technical Sales"
    assert projection.decision.original_headline == "Inside Sales"
    assert projection.decision.overridden is True
    assert projection.decision.source_label == "Changed by a person"
    assert projection.business_status.headline == "Ready for Technical Sales"
    assert any(entry.kind == "override" for entry in projection.timeline)


# ---------------------------------------------------------------------------
# §21–§25 / §64 — provenance and models
# ---------------------------------------------------------------------------


def test_an_ai_fact_names_the_model_that_executed_not_the_one_requested():
    projection = project()

    assert projection.understanding.source_label == "AI · claude-sonnet-4-5"
    assert projection.understanding.ai.requested == "auto"
    assert projection.understanding.ai.executed == "claude-sonnet-4-5"
    # The requested model is available for the technical view, never on the card.
    assert "AI · auto" not in projection.model_dump_json()


def test_model_latency_and_cost_come_from_the_ledger_and_are_never_invented():
    projection = project()

    understand = find_activity(projection, "understand")
    assert understand.ai.latency_ms == 1400
    assert understand.ai.cost_usd == 0.0018

    without_ledger = project(cost_entries=[])
    assert find_activity(without_ledger, "understand").ai.latency_ms is None
    assert find_activity(without_ledger, "understand").ai.cost_usd is None


def test_provider_fallback_is_recorded_for_the_technical_view():
    entries = [{**BASF_COST_ENTRIES[0], "fallback_used": True,
                "fallback_reason": "Provider temporarily unavailable"}]
    projection = project(cost_entries=entries)

    ai = find_activity(projection, "understand").ai
    assert ai.fallback is True
    assert ai.fallback_reason == "Provider temporarily unavailable"


def test_a_rule_based_decision_carries_no_model():
    projection = project()

    assert projection.decision.source.value == "rule"
    assert projection.decision.source_label == "Business rule"
    assert find_activity(projection, "handling").ai is None


def test_an_erp_result_is_labelled_as_a_system_of_record_not_as_ai():
    projection = project()

    enrich = find_activity(projection, "enrich")
    customer = next(fact for fact in enrich.facts if fact.label == "Customer")
    assert customer.display == "BASF SE"
    assert customer.source.value == "system"
    assert customer.source_label == "System of record"


def test_a_corrected_fact_is_attributed_to_the_person_who_corrected_it():
    run = basf_run()
    run["fact_edits"] = [{"field": "requested_quantity", "value": "6", "edited_at": "2026-08-14T18:00:00+00:00"}]
    run["outputs"]["understand_message"]["parsed"]["requested_quantity"] = "6"
    projection = project(run)

    quantity = next(f for f in projection.understanding.fields if f.label == "Quantity")
    assert quantity.source.value == "human"
    assert quantity.source_label == "Corrected by a person"
    assert any(entry.kind == "edit" for entry in projection.timeline)


# ---------------------------------------------------------------------------
# §27 / §54 / §65 — actions and permissions
# ---------------------------------------------------------------------------


def test_only_actions_valid_for_this_state_are_offered():
    projection = project()
    types = {action.type.value for action in projection.allowed_actions}

    assert "assign_work_item" in types
    assert "rerun_dependency" in types          # a completed run can be re-checked
    assert "pause_run" not in types             # nothing is running
    assert "resume_run" not in types            # nothing is paused
    assert "stop_run" not in types              # already finished


def test_a_running_work_item_can_be_paused_but_not_re_run():
    run = basf_run(status="running", ended_at=None)
    types = {action.type.value for action in project(run).allowed_actions}

    assert "pause_run" in types
    assert "stop_run" in types
    assert "rerun_dependency" not in types


def test_a_viewer_is_offered_no_action_that_changes_anything():
    projection = project(role=Role.VIEWER)
    types = {action.type.value for action in projection.allowed_actions}

    assert types <= {"open_technical_details", "open_related_record", "explain_decision"}
    assert not any(item.actions for item in projection.attention if
                   {a.type.value for a in item.actions} - {"open_related_record"})


def test_asking_the_customer_is_marked_as_requiring_approval():
    projection = project()

    ask = next(
        action for item in projection.attention for action in item.actions
        if action.type.value == "draft_clarification"
    )
    assert ask.requires_approval is True


def test_a_record_lookup_is_only_offered_for_a_tool_this_workflow_uses():
    projection = project()

    lookups = [a for r in projection.related_records for a in r.actions
               if a.type.value == "related_record_lookup"]
    assert [a.params["tool"] for a in lookups] == ["get_sales_order"]
    assert lookups[0].params["server_id"] == "dynamics365_finance_scm"


def test_recommended_actions_prefer_evidence_over_asking_the_customer():
    projection = project()

    assert projection.recommended_actions[0].label == "Open SO 231706"


# ---------------------------------------------------------------------------
# §30 / §35 / §37 — next step and business context
# ---------------------------------------------------------------------------


def test_next_step_states_who_takes_it_on_and_what_they_do():
    projection = project()

    assert projection.next_step.headline == "Inside Sales takes this on"
    assert "prepare" in projection.next_step.description.lower()
    assert projection.next_step.blocked is False


def test_related_records_carry_the_reference_the_customer_gave():
    projection = project()

    record = projection.related_records[0]
    assert record.reference == "SO 231706"
    assert record.label == "Sales order"
    assert record.source.value == "customer_message"


def test_the_work_item_is_named_after_the_customer_and_their_request():
    projection = project()

    assert projection.work_item.title == "BASF SE — Quotation request"
    assert projection.work_item.customer == "BASF SE"
    assert projection.work_item.type == "Quotation request"
    # The workflow's own name stays available as the process.
    assert projection.process.name.startswith("Pump Customer Routing")


def test_the_verified_account_name_wins_over_the_name_the_customer_typed():
    run = basf_run()
    run["outputs"]["understand_message"]["parsed"]["customer_name"] = "BASF"
    projection = project(run)

    assert projection.work_item.customer == "BASF SE"


def test_suggested_questions_follow_the_work_items_state():
    projection = project()

    assert "Why Inside Sales?" in projection.suggested_questions
    assert "What is missing?" in projection.suggested_questions
    assert "Show SO 231706" in projection.suggested_questions


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_a_run_whose_workflow_no_longer_parses_still_opens():
    projection = project(workflow_spec=None)

    assert projection.work_item.id == "run-basf-001"
    assert projection.business_status.code in ("ready_for_team", "completed", "needs_information")
    # Without the spec there are no router configs, so subjects cannot be
    # recovered — but the understanding and activities still render.
    assert projection.understanding.fields
    assert projection.activities


def test_a_workflow_with_no_extraction_step_degrades_without_crashing():
    run = {
        "run_id": "run-2", "session_id": "s", "status": "running",
        "workflow_name": "Something else", "started_at": T0, "updated_at": T0,
        "node_types": {}, "node_runs": {}, "outputs": {},
    }
    projection = build_business_projection(run, workflow_spec=None)

    assert projection.understanding.fields == []
    assert projection.attention == []
    assert projection.activities == []
    assert projection.next_step is not None


def test_the_older_triage_workflow_still_projects():
    run = {
        "run_id": "run-3", "session_id": "s", "status": "paused",
        "workflow_name": "Multilingual Customer Request Triage",
        "started_at": T0, "updated_at": T0 + 10,
        "node_types": {node.id: node.type for node in TRIAGE_SPEC.nodes},
        "node_runs": {
            "understand_request": node_run("completed", T0 + 1, T0 + 5),
            "automation_safety": node_run("completed", T0 + 5, T0 + 6),
        },
        "outputs": {
            "understand_request": {
                "result": {"intent": "spare_part_request", "missing_information": ["product_model"]},
                "confidence": 0.62,
            },
            "automation_safety": {
                "decisions": {"human_review": True, "escalation_reason": "Low confidence."},
                "matched_rules": ["Low confidence needs a person"],
                "explanation": [
                    {"name": "Low confidence needs a person", "matched": True,
                     "description": "Below 0.80 we do not act automatically."},
                    {"name": "Complaints are always handled by a person", "matched": False, "description": "n/a"},
                ],
                "summary": ["Escalated: low confidence"],
            },
        },
    }
    gate = {
        "paused": True, "pause_kind": "hitl_gate", "node_id": "human_review",
        "question": "Check the extracted information and decide how to proceed.",
        "allowed_actions": ["approve", "edit", "reject"],
    }
    projection = build_business_projection(run, workflow_spec=TRIAGE_SPEC, gate=gate)

    assert projection.business_status.code == "waiting_for_approval"
    assert "Product model" in [item.title for item in projection.attention]
    # A DecisionAgent's named decisions become the decision card, with only the
    # rule that actually matched.
    assert projection.decision is not None
    assert [rule.name for rule in projection.decision.rules if rule.node_id == "automation_safety"] == [
        "Low confidence needs a person"
    ]


def test_humanize_identifier_is_unchanged():
    assert humanize_identifier("understand_request") == "Understand request"
    assert humanize_identifier("") == "Workflow step"


def test_the_projection_serialises_cleanly():
    # FastAPI returns this model directly; a field that cannot serialise would
    # 500 the screen rather than degrade it.
    payload = json.loads(project().model_dump_json())
    assert payload["work_item"]["title"]
    assert payload["business_status"]["state_version"]
