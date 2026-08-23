"""The business timeline: what happened, once per thing that happened.

The old timeline emitted `<node> started` and `<node> completed` for every
node, so a fourteen-node run read as twenty-eight entries of which none was a
business event. Here, an activity contributes exactly one entry, at the moment
it finished, carrying its findings as supporting marks (§8, §9, §43).

What stays individually visible, always: human decisions, corrections a person
made, route overrides, and failures (§67). Those are the moments somebody is
accountable for, and collapsing them would defeat the point of a record.
"""
from __future__ import annotations

from app.workflow.business_view.common import sentence, timestamp_iso
from app.workflow.business_view.models import (
    SOURCE_LABELS,
    BusinessActivityView,
    BusinessDecisionView,
    BusinessSource,
    BusinessStatusView,
    BusinessTimelineEntry,
)
from app.workflow.business_view.runstate import RunView

#: Activities whose facts read as a checklist under the entry.
_MARK_ACTIVITIES = {"handling", "enrich", "ownership"}


def _marks(activity: BusinessActivityView) -> list[str]:
    """Internal helper for the marks step.

    Args:
        activity (BusinessActivityView): The activity.

    Returns:
        list[str]: The result.
    """
    if activity.id not in _MARK_ACTIVITIES:
        return []
    return [f"{fact.label}: {fact.display}" for fact in activity.facts[:6] if not fact.missing]


def build_timeline(
    run: RunView,
    *,
    activities: list[BusinessActivityView],
    decision: BusinessDecisionView | None,
    status: BusinessStatusView,
) -> list[BusinessTimelineEntry]:
    """Build the timeline.

    Args:
        run (RunView): The run.
        activities (list[BusinessActivityView]): The activities.
        decision (BusinessDecisionView | None): Human decision mapping.
        status (BusinessStatusView): Status value.

    Returns:
        list[BusinessTimelineEntry]: The timeline.
    """
    entries: list[BusinessTimelineEntry] = []

    if run.started_at:
        entries.append(
            BusinessTimelineEntry(
                id="timeline:received",
                ts=run.started_at,
                title="Request received",
                kind="status",
                source=BusinessSource.MESSAGE,
                source_label=SOURCE_LABELS[BusinessSource.MESSAGE],
            )
        )

    for activity in activities:
        if activity.status in ("planned", "skipped"):
            continue
        ts = activity.completed_at or activity.started_at or run.started_at
        if not ts:
            continue
        kind = "failure" if activity.status == "attention" else (
            "human" if activity.id.startswith("review:") else "activity"
        )
        source = {
            "ai": BusinessSource.AI, "rule": BusinessSource.RULE,
            "system": BusinessSource.SYSTEM, "human": BusinessSource.HUMAN,
        }.get(activity.kind, BusinessSource.WORKFLOW)
        entries.append(
            BusinessTimelineEntry(
                id=f"timeline:activity:{activity.id}",
                ts=ts,
                title=(
                    activity.title
                    if activity.status == "completed"
                    else f"{activity.title} — {activity.status_label.lower()}"
                ),
                detail=activity.summary,
                marks=_marks(activity),
                kind=kind,  # type: ignore[arg-type]
                source=source,
                source_label=activity.kind_label,
            )
        )

    if decision is not None and decision.headline:
        anchor = next(
            (a.completed_at for a in reversed(activities) if a.completed_at), run.ended_at
        )
        if anchor:
            entries.append(
                BusinessTimelineEntry(
                    id="timeline:decision",
                    ts=anchor,
                    title=f"Routed to {decision.headline}",
                    detail=decision.reason,
                    kind="activity",
                    source=decision.source,
                    source_label=decision.source_label,
                )
            )

    for index, edit in enumerate(run.fact_edits):
        ts = timestamp_iso(edit.get("edited_at"))
        if not ts:
            continue
        entries.append(
            BusinessTimelineEntry(
                id=f"timeline:edit:{index}",
                ts=ts,
                title=f"{str(edit.get('field', '')).replace('_', ' ').capitalize()} corrected by a person",
                detail=sentence(f"changed to {edit.get('value')}") if edit.get("value") is not None else None,
                kind="edit",
                source=BusinessSource.HUMAN,
                source_label=SOURCE_LABELS[BusinessSource.HUMAN],
            )
        )

    for index, override in enumerate(run.route_overrides):
        ts = timestamp_iso(override.get("at"))
        if not ts:
            continue
        entries.append(
            BusinessTimelineEntry(
                id=f"timeline:override:{index}",
                ts=ts,
                title=f"Route changed to {override.get('route')}",
                detail=sentence(str(override.get("reason"))) if override.get("reason") else None,
                kind="override",
                source=BusinessSource.HUMAN,
                source_label=f"Changed by {override.get('by')}" if override.get("by") else SOURCE_LABELS[BusinessSource.HUMAN],
            )
        )

    for index, note in enumerate(run.notes):
        ts = timestamp_iso(note.get("at"))
        if not ts:
            continue
        entries.append(
            BusinessTimelineEntry(
                id=f"timeline:note:{index}",
                ts=ts,
                title="Note added",
                detail=str(note.get("text", "")),
                kind="edit",
                source=BusinessSource.HUMAN,
                source_label=f"{note.get('by')}" if note.get("by") else SOURCE_LABELS[BusinessSource.HUMAN],
            )
        )

    if run.ended_at and run.is_finished:
        entries.append(
            BusinessTimelineEntry(
                id="timeline:final",
                ts=run.ended_at,
                title=status.headline,
                kind="status",
                source=BusinessSource.WORKFLOW,
                source_label=SOURCE_LABELS[BusinessSource.WORKFLOW],
            )
        )

    entries.sort(key=lambda entry: (entry.ts or "", entry.id))
    return entries


def happened(activities: list[BusinessActivityView], decision: BusinessDecisionView | None) -> list[str]:
    """The compact "What happened" checklist shown before the full history (§18)."""
    lines: list[str] = []
    for activity in activities:
        if activity.status != "completed":
            continue
        if activity.id in _MARK_ACTIVITIES and activity.facts:
            # A fact the workflow could not establish belongs in the attention
            # centre, not in a list of things that went right.
            lines.extend(
                f"{fact.label}: {fact.display}"
                for fact in activity.facts[:5]
                if not fact.missing
            )
        elif activity.summary:
            lines.append(activity.summary)
        else:
            lines.append(activity.title)
    if decision is not None and decision.headline:
        lines.append(f"Routed to {decision.headline}")
    # The checklist is a glance, not a transcript.
    return list(dict.fromkeys(lines))[:8]
