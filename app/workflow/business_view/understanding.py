"""«What I understood» as business fields — the replacement for the JSON dump.

The old Business View printed the extraction node's output verbatim: a `raw`
string of model JSON next to a `parsed` object. This module turns the same
data into labelled, ordered, provenance-carrying business facts, and drops the
machinery (`raw`, `confidence`, `missing_information`) that belongs elsewhere
on the screen or nowhere on it (§4, §5, §39).
"""
from __future__ import annotations

from typing import Any

from app.workflow.business_view.actions import ActionFactory
from app.workflow.business_view.activities import extraction_payload, find_understanding_node
from app.workflow.business_view.common import (
    UNDERSTANDING_FIELD_ORDER,
    UNDERSTANDING_INTERNAL_KEYS,
    compact_text,
    field_label,
    format_value,
    is_empty_value,
    sentence,
)
from app.workflow.business_view.models import (
    SOURCE_LABELS,
    BusinessFact,
    BusinessSource,
    BusinessUnderstanding,
)
from app.workflow.business_view.runstate import NodeView, RunView, model_usage
from app.workflow.fact_corrections import derive_dependencies, payload_key_for


def _ordered(keys: list[str]) -> list[str]:
    """Business-priority order first, then whatever the workflow also extracted."""
    known = [key for key in UNDERSTANDING_FIELD_ORDER if key in keys]
    rest = [key for key in keys if key not in UNDERSTANDING_FIELD_ORDER]
    return known + rest


def editable_fields(payload: dict[str, Any]) -> set[str]:
    """Which extracted fields a person may correct in place.

    Every scalar or list business field qualifies (§33) — a salesperson
    correcting a quantity should not need to know whether a rule happens to
    read it. Nested objects are excluded: editing one in a text box would
    require the person to write JSON, which is the thing being removed.
    """
    return {
        key
        for key, value in payload.items()
        if key not in UNDERSTANDING_INTERNAL_KEYS
        and not isinstance(value, dict)
    }


def build_understanding(
    run: RunView, factory: ActionFactory,
) -> tuple[BusinessUnderstanding, NodeView | None, dict[str, Any]]:
    """The understanding block, plus the node and payload other parts reuse."""
    node = find_understanding_node(run)
    payload = extraction_payload(node)
    if node is None or not payload:
        return BusinessUnderstanding(), node, {}

    ai = model_usage([node])
    ai_label = f"AI · {ai.executed}" if ai and ai.executed else SOURCE_LABELS[BusinessSource.AI]
    edited = {edit.get("field") for edit in run.fact_edits if edit.get("field")}
    editable = editable_fields(payload)
    dependencies = derive_dependencies(run.spec, node.node_id)
    stale = set(run.stale_decisions)

    fields: list[BusinessFact] = []
    for key in _ordered(list(payload)):
        if key in UNDERSTANDING_INTERNAL_KEYS:
            continue
        value = payload[key]
        if isinstance(value, dict):
            # A nested object is not a business fact; it is a payload. It stays
            # available under technical details rather than being flattened
            # into something that reads like JSON.
            continue
        was_edited = key in edited
        source = BusinessSource.HUMAN if was_edited else BusinessSource.AI
        fields.append(
            BusinessFact(
                id=f"understanding:{key}",
                label=field_label(key),
                value=value,
                display=format_value(value, key=key),
                source=source,
                source_label="Corrected by a person" if was_edited else ai_label,
                node_id=node.node_id,
                editable=key in editable and factory.edit_fact(key) is not None,
                stale=bool(set(dependencies.get(key, ())) & stale),
                missing=is_empty_value(value),
                actions=[action for action in [factory.edit_fact(key, label="Edit")] if action],
            )
        )

    confidence = payload.get("confidence")
    summary = compact_text(payload.get("english_summary") or payload.get("summary"))

    actions = [
        action
        for action in (
            factory.explain(target="understanding"),
            factory.technical_details("understand", label="View technical details"),
        )
        if action
    ]

    return (
        BusinessUnderstanding(
            node_id=node.node_id,
            summary=sentence(summary) if summary else None,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            fields=fields,
            source=BusinessSource.AI,
            source_label=ai_label,
            ai=ai,
            actions=actions,
        ),
        node,
        payload,
    )


def correction_target(run: RunView) -> tuple[str, str, set[str]] | None:
    """(node_id, payload key, editable fields) for this run's extraction node.

    Returned to the fact-correction endpoint so it writes to the right path for
    whichever workflow this run used, instead of assuming one workflow's shape.
    """
    node = find_understanding_node(run)
    if node is None:
        return None
    payload = extraction_payload(node)
    if not payload:
        return None
    return node.node_id, payload_key_for(node.output), editable_fields(payload)
