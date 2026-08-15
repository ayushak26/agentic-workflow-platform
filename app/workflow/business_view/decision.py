"""The handling decision, and the facts and rules behind it (§19, §20).

Three sources, in order of how directly they state a business outcome:

1. **The terminal handoff note.** These workflows end every branch in a short
   note addressed to the receiving team — "Primary team: Inside Sales",
   "Reason: standard enquiry…". That is the author's own statement of the
   decision, and it is what the card leads with.
2. **A `DecisionAgent`.** Named decision fields plus the rule trace that set
   them, for workflows that express handling as decisions rather than routes.
3. **The routers that ran.** Their branches and matched conditions are the
   rules; the subjects they tested are the supporting facts.

A human route override sits on top of all three, clearly marked, because a
person's decision must never be quietly presented as the system's.
"""
from __future__ import annotations

from app.workflow.business_view.actions import ActionFactory
from app.workflow.business_view.activities import (
    HANDLING,
    outcome_note,
    router_fact,
)
from app.workflow.business_view.common import (
    compact_text,
    field_label,
    format_value,
    humanize_case_title,
    humanize_identifier,
    sentence,
    title_case_team,
)
from app.workflow.business_view.models import (
    SOURCE_LABELS,
    BusinessDecisionView,
    BusinessFact,
    BusinessRule,
    BusinessSource,
)
from app.workflow.business_view.routers import DECISION_TYPES, router_findings
from app.workflow.business_view.runstate import RunView


def _decision_agent_view(run: RunView) -> tuple[dict, list[BusinessRule], list[BusinessFact]] | None:
    """Decisions, matched rules and decision facts from a `DecisionAgent`."""
    for node in run.nodes:
        if node.type_name not in DECISION_TYPES or not node.succeeded:
            continue
        output = node.output_dict()
        decisions = output.get("decisions")
        if not isinstance(decisions, dict) or not decisions:
            continue
        rules = [
            BusinessRule(
                id=f"{node.node_id}:{rule.get('name', '')}",
                name=str(rule.get("name", "")),
                description=compact_text(rule.get("description")),
                node_id=node.node_id,
                matched=True,
            )
            for rule in output.get("explanation") or []
            if isinstance(rule, dict) and rule.get("matched") and rule.get("name")
        ]
        stale = set(run.stale_decisions)
        facts = [
            BusinessFact(
                id=f"decision:{key}",
                label=field_label(key),
                value=value,
                display=format_value(value, key=key),
                source=BusinessSource.RULE,
                source_label=SOURCE_LABELS[BusinessSource.RULE],
                node_id=node.node_id,
                stale=key in stale,
            )
            for key, value in decisions.items()
        ]
        return decisions, rules, facts
    return None


#: Decision fields that name where a case goes.
_ROUTE_KEYS = ("route", "team", "primary_intent", "department", "queue", "owner", "assigned_team")
#: Decision fields that explain a decision rather than being one.
_REASON_KEYS = ("escalation_reason", "reason", "rationale", "explanation")


def _agent_headline(decisions: dict) -> str | None:
    """The outcome a `DecisionAgent`'s named fields amount to.

    A workflow that decides "human_review = true, escalation_reason = low
    confidence" has made a handling decision — *a person handles this* — even
    though no field is literally called `route`. Reason fields are excluded
    explicitly: an explanation is not an outcome.
    """
    for key in _ROUTE_KEYS:
        value = decisions.get(key)
        if isinstance(value, str) and value.strip():
            return humanize_identifier(value)

    for key, value in decisions.items():
        if value is True and "review" in key:
            return "Human review"

    for key, value in decisions.items():
        if key not in _REASON_KEYS and isinstance(value, str) and value.strip():
            return humanize_identifier(value)
    return None


def _router_rules(run: RunView) -> list[BusinessRule]:
    """One rule entry per router that ran, in the author's own wording."""
    rules: list[BusinessRule] = []
    for finding in router_findings(run):
        subject = (
            field_label(finding.subject_key)
            if finding.subject_key
            else humanize_identifier(finding.node_id)
        )
        rules.append(
            BusinessRule(
                id=finding.node_id,
                name=f"{subject} → {finding.route_label}",
                description=compact_text(finding.reason),
                node_id=finding.node_id,
                matched=not finding.used_fallback,
            )
        )
    return rules


def build_decision(
    run: RunView, factory: ActionFactory, handling_facts: list[BusinessFact],
) -> BusinessDecisionView | None:
    """The handling decision, or None when this run has not made one yet."""
    note_entry = outcome_note(run)
    agent_view = _decision_agent_view(run)
    findings = router_findings(run)

    headline: str | None = None
    summary: str | None = None
    reason: str | None = None
    node_ids: list[str] = []
    facts = list(handling_facts)
    rules: list[BusinessRule] = []

    if note_entry is not None:
        node, note = note_entry
        node_ids.append(node.node_id)
        team = note.get("team") or note.get("owner")
        if team:
            headline = title_case_team(team)
        summary = humanize_case_title(note["case_title"]) if note.get("case_title") else None
        reason = sentence(note["reason"]) if note.get("reason") else None
        for slot, label in (("owner", "Owner"), ("commercial_owner", "Commercial owner"),
                            ("supporting_team", "Supporting team")):
            if note.get(slot):
                facts.append(
                    BusinessFact(
                        id=f"decision:{slot}",
                        label=label,
                        value=note[slot],
                        display=note[slot],
                        source=BusinessSource.RULE,
                        source_label=SOURCE_LABELS[BusinessSource.RULE],
                        node_id=node.node_id,
                    )
                )

    if agent_view is not None:
        decisions, agent_rules, agent_facts = agent_view
        rules.extend(agent_rules)
        facts.extend(agent_facts)
        if headline is None:
            headline = _agent_headline(decisions)
        if reason is None:
            reason = next(
                (
                    sentence(str(value))
                    for key, value in decisions.items()
                    if key in _REASON_KEYS and isinstance(value, str) and value.strip()
                ),
                None,
            )

    if headline is None and findings:
        # No handoff note and no decision agent: the last route taken is the
        # most specific statement of what happened that this run contains.
        headline = findings[-1].route_label

    if headline is None:
        return None

    rules.extend(_router_rules(run))
    node_ids.extend(finding.node_id for finding in findings)

    override = run.route_overrides[-1] if run.route_overrides else None
    original = None
    overridden_by = overridden_at = None
    if override:
        original, headline = headline, str(override.get("route") or headline)
        overridden_by = override.get("by")
        overridden_at = override.get("at")
        reason = sentence(str(override.get("reason"))) if override.get("reason") else reason

    actions = [
        action
        for action in (
            factory.explain(target="decision"),
            factory.route_override(current=headline),
            factory.assign(suggested=headline),
            factory.recheck(),
            factory.technical_details(HANDLING),
        )
        if action
    ]

    return BusinessDecisionView(
        id="handling_decision",
        headline=headline,
        summary=summary,
        reason=reason,
        source=BusinessSource.HUMAN if override else BusinessSource.RULE,
        source_label=(
            "Changed by a person" if override else SOURCE_LABELS[BusinessSource.RULE]
        ),
        facts=facts,
        rules=rules,
        actions=actions,
        node_ids=list(dict.fromkeys(node_ids)),
        overridden=bool(override),
        overridden_by=overridden_by,
        overridden_at=overridden_at,
        original_headline=original,
        stale=any(fact.stale for fact in facts),
    )


def handling_facts(run: RunView) -> list[BusinessFact]:
    """The supporting "✓ RFQ / ✓ Standard complexity / ✓ No safety issue" facts."""
    return [
        router_fact(finding, run)
        for finding in router_findings(run)
        if not finding.is_ownership
    ]
