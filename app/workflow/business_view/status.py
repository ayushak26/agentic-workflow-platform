"""The authoritative business status, and what happens next.

Status is computed from workflow state — run status, pause kind, which
business activity is running, whether a handling decision has been reached —
and never from the latest timeline event or from a model (§13). A narrator may
later rephrase the wording; it may not change `code` or `tone`, and the screen
renders correctly if it never runs at all (§16).

`state_version` is here too: it is the hash of everything a business user
would notice changing, and therefore exactly the right cache key for the
narration (§17).
"""
from __future__ import annotations

import hashlib
import json

from app.workflow.business_view.actions import ActionFactory
from app.workflow.business_view.activities import active_activity, running_headline
from app.workflow.business_view.common import sentence, title_case_team
from app.workflow.business_view.models import (
    BusinessActivityView,
    BusinessAttentionItem,
    BusinessDecisionView,
    BusinessNextStep,
    BusinessStatusView,
    BusinessUnderstanding,
)
from app.workflow.business_view.runstate import RunView

READY_FOR_TEAM = "ready_for_team"
NEEDS_INFORMATION = "needs_information"
WAITING_FOR_APPROVAL = "waiting_for_approval"
PAUSED = "paused"
BLOCKED = "blocked"
FAILED = "failed"
STOPPED = "stopped"
COMPLETED = "completed"
IN_PROGRESS = "in_progress"


def _attention_count(items: list[BusinessAttentionItem]) -> int:
    return len(items)


def build_status(
    run: RunView,
    *,
    activities: list[BusinessActivityView],
    attention: list[BusinessAttentionItem],
    decision: BusinessDecisionView | None,
    understanding: BusinessUnderstanding,
    customer: str | None,
) -> BusinessStatusView:
    """The one status line a business user reads first."""
    count = _attention_count(attention)
    summary = understanding.summary or sentence(run.goal)

    if run.run_status == "failed":
        failed = next((a for a in activities if a.status == "attention"), None)
        return _status(
            FAILED,
            "Needs attention",
            sentence(
                f"{failed.title} did not complete. Nothing was changed in any other system"
                if failed
                else "This work item stopped before it finished. Nothing was changed in any other system"
            ),
            "attention",
            count,
            run,
        )

    if run.run_status == "rejected":
        return _status(STOPPED, "Stopped", sentence("This work item was stopped and not completed"), "stopped", count, run)

    if run.awaits_approval:
        question = (run.gate or {}).get("question") or ""
        return _status(
            WAITING_FOR_APPROVAL,
            "Waiting for approval",
            sentence(question) if question else sentence("Someone needs to review this before it continues"),
            "waiting",
            count,
            run,
        )

    if run.is_paused:
        return _status(PAUSED, "Paused", sentence("This work is paused and can be resumed when you're ready"), "waiting", count, run)

    if run.run_status in ("running", "resuming"):
        active = active_activity(activities)
        headline = (running_headline(active.id) if active else None) or "Working on this request"
        return _status(IN_PROGRESS, headline, summary, "progress", count, run)

    # Finished. What matters now is where it landed, not that it finished.
    if decision is not None and decision.headline:
        team = title_case_team(decision.headline)
        return _status(READY_FOR_TEAM, f"Ready for {team}", summary, "done", count, run)

    if count:
        return _status(
            NEEDS_INFORMATION,
            "Waiting for missing information",
            summary,
            "attention",
            count,
            run,
        )

    return _status(COMPLETED, "Completed", summary, "done", count, run)


def _status(code, headline, summary, tone, count, run) -> BusinessStatusView:
    return BusinessStatusView(
        code=code,
        headline=headline,
        summary=summary,
        tone=tone,
        attention_count=count,
        state_version="",  # filled in by state_version() once the projection is whole
    )


def build_next_step(
    run: RunView,
    *,
    status: BusinessStatusView,
    decision: BusinessDecisionView | None,
    attention: list[BusinessAttentionItem],
    handoff_action: str | None,
    factory: ActionFactory,
) -> BusinessNextStep:
    """"What happens next" — always answered, blocked or not (§30)."""
    blocking = [item for item in attention if item.severity == "blocking"]

    if status.code == FAILED:
        return BusinessNextStep(
            headline="Decide how to recover",
            description="Retry the unfinished work, or record what you did instead.",
            blocked=True,
            blocked_reason="This work item stopped before it finished.",
            actions=[a for a in (factory.recheck(), factory.add_note(), factory.assign()) if a],
        )

    if status.code == WAITING_FOR_APPROVAL:
        return BusinessNextStep(
            headline="Review and respond",
            description=(run.gate or {}).get("question") or "This work item is waiting on your decision.",
            blocked=True,
            blocked_reason="Nothing continues until someone reviews this.",
            actions=[a for a in (factory.approve(), factory.stop()) if a],
        )

    if status.code == PAUSED:
        return BusinessNextStep(
            headline="Resume when you're ready",
            blocked=True,
            blocked_reason="This work is paused.",
            actions=[a for a in (factory.resume(), factory.stop()) if a],
        )

    if status.code == IN_PROGRESS:
        return BusinessNextStep(
            headline="Nothing needed from you yet",
            description="This work item is still running. You'll be asked if a decision is needed.",
            actions=[a for a in (factory.pause(), factory.stop()) if a],
        )

    if blocking:
        item = blocking[0]
        return BusinessNextStep(
            headline=f"Resolve: {item.title.lower()}",
            description=item.detail,
            blocked=True,
            blocked_reason=f"The process cannot continue until {item.title.lower()} is resolved.",
            actions=item.actions,
        )

    owner = title_case_team(decision.headline) if decision and decision.headline else None
    description = sentence(handoff_action) if handoff_action else (
        f"{owner} reviews this request and prepares the response." if owner else None
    )
    actions = [
        a
        for a in (
            factory.assign(suggested=owner),
            factory.add_note(),
            factory.recheck(),
        )
        if a
    ]
    return BusinessNextStep(
        headline=f"{owner} takes this on" if owner else "Nothing is outstanding",
        description=description,
        owner=owner,
        actions=actions,
    )


def state_version(
    *,
    status: BusinessStatusView,
    decision: BusinessDecisionView | None,
    attention: list[BusinessAttentionItem],
    activities: list[BusinessActivityView],
    understanding: BusinessUnderstanding,
) -> str:
    """A stable hash of everything a business user would notice changing.

    Deliberately excludes timestamps, durations, costs and node ids: a run
    whose only change is that it took 40ms longer has not changed what the
    status line should say, and re-narrating it would spend a model call to
    produce the same sentence (§17, §50).
    """
    material = {
        "code": status.code,
        "attention": [f"{item.id}:{item.status_label}" for item in attention],
        "decision": decision.headline if decision else None,
        "overridden": decision.overridden if decision else False,
        "activities": [f"{a.id}:{a.status}" for a in activities],
        "understanding": [f"{f.id}={f.display}" for f in understanding.fields],
        "summary": understanding.summary,
    }
    encoded = json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]
