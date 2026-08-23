"""Assembling one Work Item screen (§45).

    Run document + workflow spec + gate + cost ledger entries
                              │
                              ▼
                  build_business_projection()
                              │
                              ▼
                       Business View

Pure and read-only: this function performs no I/O and mutates nothing. The
API route fetches the run, the pending gate and the ledger entries; everything
here is reshaping. That is what makes the whole projection testable with plain
dictionaries, and what keeps a rendering bug from ever being able to corrupt a
run.

Deliberately absent from the output: raw model text, parsed payloads, prompts,
node-level inputs. A business user's screen cannot leak them because they are
not in the payload — see app/api/runs.py's technical-detail route for where
they live instead (§5, §46, §60).
"""
from __future__ import annotations

from typing import Any

from app.runtime.schema import WorkflowSpec
from app.security.rbac import Role
from app.workflow.business_view.actions import ActionContext, ActionFactory
from app.workflow.business_view.activities import build_activities, outcome_note
from app.workflow.business_view.attention import best_action, build_attention
from app.workflow.business_view.common import (
    VALUE_LABELS,
    humanize_identifier,
    is_empty_value,
)
from app.workflow.business_view.context import (
    build_attachments,
    build_related_records,
    customer_name,
)
from app.workflow.business_view.decision import build_decision, handling_facts
from app.workflow.business_view.models import (
    BusinessAction,
    BusinessActionType,
    BusinessProcess,
    BusinessProjection,
    BusinessRequiredUserAction,
    BusinessWorkItem,
)
from app.workflow.business_view.runstate import build_run_view
from app.workflow.business_view.status import (
    IN_PROGRESS,
    WAITING_FOR_APPROVAL,
    build_next_step,
    build_status,
    state_version,
)
from app.workflow.business_view.timeline import build_timeline, happened
from app.workflow.business_view.understanding import build_understanding, editable_fields
from app.workflow.fact_corrections import derive_dependencies


def _work_item_title(customer: str | None, work_type: str, process_name: str) -> str:
    """"BASF RFQ" rather than "Pump Manufacturer Multilingual Customer Case Routing".

    A work item is named after the customer and what they want, because that is
    how the person handling it refers to it. The workflow's own name is the
    *process*, and it stays available as such.
    """
    if customer and work_type:
        return f"{customer} — {work_type}"
    return customer or work_type or process_name or "Work item"


def _request_type(payload: dict[str, Any]) -> str:
    """Internal helper for the request type step.

    Args:
        payload (dict[str, Any]): Event or audit payload.

    Returns:
        str: The type.
    """
    for key in ("intent", "primary_intent", "request_types"):
        value = payload.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, str) and not is_empty_value(value):
            return VALUE_LABELS.get(value, humanize_identifier(value))
    return ""


def _required_user_actions(run) -> list[BusinessRequiredUserAction]:
    """Internal helper for the required user actions step.

    Args:
        run: The run.

    Returns:
        list[BusinessRequiredUserAction]: The user actions.
    """
    gate = run.gate or {}
    if not gate.get("paused"):
        return []
    if gate.get("pause_kind") == "user_requested":
        return [
            BusinessRequiredUserAction(
                type="resume_decision",
                node_id=gate.get("node_id"),
                message="This work is paused. Resume it when you're ready.",
            )
        ]
    return [
        BusinessRequiredUserAction(
            type="approval_review",
            node_id=gate.get("node_id"),
            question=gate.get("question", ""),
            allowed_actions=gate.get("allowed_actions") or ["approve", "reject"],
        )
    ]


def _suggested_questions(*, status_code: str, decision, attention, records) -> list[str]:
    """Prompt chips that change with the work item's state (§31)."""
    questions: list[str] = []
    if decision is not None and decision.headline:
        questions.append(f"Why {decision.headline}?")
    if attention:
        questions.append("What is missing?")
    if records:
        questions.append(f"Show {records[0].reference}")
    if status_code in (WAITING_FOR_APPROVAL, IN_PROGRESS):
        questions.append("What is this waiting for?")
    questions.append("What should I do next?")
    return list(dict.fromkeys(questions))[:4]


def _dedupe(actions: list[BusinessAction], *, by_label: bool = False) -> list[BusinessAction]:
    """Drop repeats. Two buttons reading the same is a bug, whatever their ids.

    `by_label` collapses actions that differ only in which activity they point
    at — the Action Center wants one "Technical details", not one per activity.
    """
    seen: set[str] = set()
    unique: list[BusinessAction] = []
    for action in actions:
        marker = f"{action.type.value}:{action.label}" if by_label else action.id
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(action)
    return unique


def build_business_projection(
    run_doc: dict[str, Any],
    *,
    workflow_spec: WorkflowSpec | None,
    gate: dict[str, Any] | None = None,
    cost_entries: list[dict[str, Any]] | None = None,
    role: Role | str = Role.CONSULTANT,
) -> BusinessProjection:
    """Reshape one run into everything its Business View screen needs.

    `workflow_spec` may be None — a run whose saved YAML no longer parses still
    opens, with fewer labels rather than an error. `role` gates which actions
    are offered; `cost_entries` (from the cost ledger) supply the model, cost
    and latency figures, and their absence simply means those are not shown.
    """
    run = build_run_view(
        run_doc, workflow_spec=workflow_spec, gate=gate, cost_entries=cost_entries
    )
    factory = ActionFactory(ActionContext(run=run, role=Role(role)))

    understanding, _understanding_node, payload = build_understanding(run, factory)
    activities = build_activities(run, factory)
    attachments = build_attachments(run, factory)
    records = build_related_records(run, payload, factory)
    customer = customer_name(run, payload)

    rule_linked = set(
        derive_dependencies(run.spec, understanding.node_id or "")
    ) if understanding.node_id else set()
    attention = build_attention(
        run, payload, factory,
        attachments=attachments,
        records=records,
        editable=editable_fields(payload),
        rule_linked=rule_linked,
    )
    decision = build_decision(run, factory, handling_facts(run))
    status = build_status(
        run,
        activities=activities,
        attention=attention,
        decision=decision,
        understanding=understanding,
        customer=customer,
    )
    status.state_version = state_version(
        status=status,
        decision=decision,
        attention=attention,
        activities=activities,
        understanding=understanding,
    )

    note_entry = outcome_note(run)
    next_step = build_next_step(
        run,
        status=status,
        decision=decision,
        attention=attention,
        handoff_action=(note_entry[1].get("action") if note_entry else None),
        factory=factory,
    )

    timeline = build_timeline(run, activities=activities, decision=decision, status=status)

    # Recommended first, then everything else that is valid — so the primary
    # move is obvious without hiding the alternatives in a menu (§29).
    recommended = _dedupe(
        [action for item in attention[:2] if (action := best_action(item))]
        + list(next_step.actions[:1])
    )[:3]
    control_actions = _dedupe(
        [
            action
            for action in (
                factory.approve(),
                factory.resume(),
                factory.pause(),
                factory.recheck(),
                factory.assign(),
                factory.add_note(),
                factory.stop(),
            )
            if action
        ]
    )
    other = _dedupe(
        [action for record in records for action in record.actions]
        + [action for attachment in attachments for action in attachment.actions]
        + (decision.actions if decision else [])
        + control_actions
    )
    other = [
        action
        for action in _dedupe(other, by_label=True)
        if action.id not in {a.id for a in recommended}
    ]

    allowed = _dedupe(
        recommended
        + other
        + [factory.technical_details("run", label="Technical details")]
        + list(understanding.actions),
        by_label=True,
    )
    # An approval-gated action must never be presented as something that just
    # happens when clicked.
    for action in allowed:
        if action.type is BusinessActionType.DRAFT_CLARIFICATION:
            action.requires_approval = True

    work_type = _request_type(payload) or humanize_identifier(run_doc.get("workflow_name", ""))

    return BusinessProjection(
        work_item=BusinessWorkItem(
            id=run.run_id,
            title=_work_item_title(customer, work_type, run.workflow_name),
            type=work_type or "Work item",
            reference=run.run_id[:8],
            started_at=run.started_at,
            updated_at=run.updated_at,
            assigned_to=run.assigned_to,
            customer=customer,
        ),
        process=BusinessProcess(name=run.workflow_name, goal=run.goal),
        status=run.run_status,
        business_status=status,
        attention=attention,
        understanding=understanding,
        activities=activities,
        happened=happened(activities, decision),
        facts=[fact for activity in activities for fact in activity.facts],
        decision=decision,
        recommended_actions=recommended,
        other_actions=other,
        next_step=next_step,
        related_records=records,
        attachments=attachments,
        timeline=timeline,
        allowed_actions=allowed,
        required_user_actions=_required_user_actions(run),
        suggested_questions=_suggested_questions(
            status_code=status.code, decision=decision, attention=attention, records=records,
        ),
        activity_summary={
            "completed": sum(1 for a in activities if a.status == "completed"),
            "total": len(activities),
            "technical_nodes": sum(len(a.source_nodes) for a in activities),
        },
    )
