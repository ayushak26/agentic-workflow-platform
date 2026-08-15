"""The Attention Center — what needs a person, and how they can resolve it.

A list of missing field names is not an attention centre; it is a complaint.
For every gap this module asks the questions in §7 — can a document resolve
it, can a related record, can the person just type it, does the customer have
to be asked — and attaches the actions that follow. An item with no possible
resolution is still listed, because a gap nobody can close is exactly the
thing a person needs to know about.

Attention comes from four places, all deterministic:

* `missing_information` the extraction node reported;
* checks that could not be performed (an ERP that did not answer) (§42);
* ambiguity a rule detected (several matching customer accounts);
* facts a person corrected, whose downstream determinations are now stale.
"""
from __future__ import annotations

import re
from typing import Any

from app.workflow.business_view.actions import ActionFactory
from app.workflow.business_view.common import (
    compact_text,
    field_label,
    humanize_identifier,
    is_empty_value,
    sentence,
)
from app.workflow.business_view.models import (
    BusinessAction,
    BusinessActionType,
    BusinessAttachment,
    BusinessAttentionItem,
    BusinessRelatedRecord,
)
from app.workflow.business_view.runstate import RunView

#: Missing-information phrasing → the extraction field it refers to. The
#: extraction prompt asks for free text ("technical specifications from the
#: attached datasheet"), so matching is by keyword, and an unmatched item is
#: simply shown without a field-specific action rather than mis-mapped.
_FIELD_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"pump\s*model|product\s*model|\bmodel\b|product\s*(type|reference)", re.I), "pump_model"),
    (re.compile(r"serial", re.I), "serial_number"),
    (re.compile(r"deliver(y|ies)?\s*date|lead\s*time|required\s*date", re.I), "requested_delivery_date"),
    (re.compile(r"quantit|how many|number of (units|pumps)", re.I), "requested_quantity"),
    (re.compile(r"contact\s*(name|person)|who to contact", re.I), "contact_name"),
    (re.compile(r"customer\s*name|company\s*name|account", re.I), "customer_name"),
    (re.compile(r"(sales\s*)?order\s*(reference|number)|\bso\b", re.I), "sales_order_reference"),
    (re.compile(r"quotation|\bquote\b", re.I), "quotation_reference"),
    (re.compile(r"\bpo\b|purchase\s*order", re.I), "customer_po_reference"),
    (re.compile(r"spare\s*part", re.I), "spare_parts"),
    (re.compile(r"technical\s*spec|datasheet|data\s*sheet|drawing|curve|duty\s*point", re.I), "technical_specifications"),
]

#: Terms that mean "the answer is in an attached document".
_DOCUMENT_TERMS = re.compile(r"datasheet|data\s*sheet|drawing|specification|attach|document|pdf", re.I)


def _field_for(text: str, known: set[str]) -> str | None:
    """Which extracted field this free-text gap refers to, if any.

    The workflow's own field names are consulted first: a run that extracts
    `product_model` must not have its gap relabelled "Pump model" because a
    keyword hint happens to fire. The hints only ever resolve to a field this
    run actually has — and `spare_parts` / `technical_specifications` are
    allowed through as the two gap kinds that name no single field.
    """
    normalised = re.sub(r"[_./-]+", " ", text).strip().lower()
    snake = re.sub(r"\s+", "_", normalised)
    if snake in known:
        return snake
    for field in known:
        if field.replace("_", " ") in normalised:
            return field

    for pattern, field in _FIELD_HINTS:
        if pattern.search(normalised):
            return field if field in known or field in _FIELDLESS_GAPS else None
    return None


#: Gap kinds that describe a body of information rather than one field, so
#: they have no key in the extraction to check against.
_FIELDLESS_GAPS = {"spare_parts", "technical_specifications"}


def _title_for(text: str, field: str | None) -> str:
    """A short business title for one gap."""
    if field:
        return field_label(field)
    cleaned = compact_text(re.sub(r"[_]+", " ", text), 90) or humanize_identifier(text)
    cleaned = re.sub(r"^(missing|no|unknown|unspecified)\s+", "", cleaned, flags=re.I)
    return cleaned[0].upper() + cleaned[1:] if cleaned else "Missing information"


def _resolution_actions(
    *,
    text: str,
    field: str | None,
    factory: ActionFactory,
    attachments: list[BusinessAttachment],
    records: list[BusinessRelatedRecord],
    editable: set[str],
) -> tuple[list[BusinessAction], str]:
    """Every way this gap can actually be closed, most direct first.

    Returns the actions and the status line describing where the answer might
    already be ("Available in an attached document") — which is the difference
    between "missing" and "missing, but we know where to look".
    """
    actions: list[BusinessAction] = []
    status = "Missing"

    mentions_document = bool(_DOCUMENT_TERMS.search(text))
    if attachments and (mentions_document or field in ("pump_model", "technical_specifications")):
        actions.extend(attachments[0].actions)
        if mentions_document:
            status = f"May be in {attachments[0].name}"

    # A reference the customer already gave is the cheapest place to look next.
    for record in records:
        if field == "spare_parts" and record.kind in ("order", "related order"):
            actions.extend(record.actions)
            status = "Not specified"
            break
        if field and record.id == f"record:{field}":
            actions.extend(record.actions)
            break
    else:
        if field in ("pump_model", "serial_number", "requested_quantity"):
            for record in records:
                if record.kind in ("order", "related order", "quotation"):
                    actions.extend(record.actions)
                    break

    if field and field in editable:
        manual = factory.edit_fact(field)
        if manual is not None:
            actions.append(manual)

    ask = factory.draft_clarification(topic=field or text)
    if ask is not None:
        actions.append(ask)

    # Two buttons that do the same thing read as a bug.
    unique: list[BusinessAction] = []
    seen: set[str] = set()
    for action in actions:
        if action.id in seen:
            continue
        seen.add(action.id)
        unique.append(action)
    return unique, status


def _missing_information(run: RunView, payload: dict[str, Any]) -> list[str]:
    """Everything the workflow itself reported as materially missing."""
    items: list[str] = []
    seen: set[str] = set()
    candidates = payload.get("missing_information")
    for node in run.nodes:
        output = node.output_dict()
        for key in ("parsed", "result"):
            nested = output.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("missing_information"), list):
                for item in nested["missing_information"]:
                    if isinstance(item, str) and item.strip() and item not in seen:
                        seen.add(item)
                        items.append(item.strip())
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, str) and item.strip() and item not in seen:
                seen.add(item)
                items.append(item.strip())
    return items


#: Action types that resolve a gap from evidence the platform already holds,
#: rather than by asking a person to type or a customer to reply. Ranked first
#: everywhere, because "look at the datasheet you already have" beats "email
#: the customer and wait two days".
_EVIDENCE_ACTIONS = {
    BusinessActionType.DOCUMENT_REVIEW,
    BusinessActionType.RELATED_RECORD_LOOKUP,
    BusinessActionType.OPEN_RELATED_RECORD,
}


def best_action(item: BusinessAttentionItem) -> BusinessAction | None:
    """The action to recommend for this item — evidence first (§28)."""
    for action in item.actions:
        if action.type in _EVIDENCE_ACTIONS:
            return action
    return item.actions[0] if item.actions else None


def build_attention(
    run: RunView,
    payload: dict[str, Any],
    factory: ActionFactory,
    *,
    attachments: list[BusinessAttachment],
    records: list[BusinessRelatedRecord],
    editable: set[str],
    rule_linked: set[str] | None = None,
) -> list[BusinessAttentionItem]:
    """Everything on this work item that wants a person, worst first."""
    items: list[BusinessAttentionItem] = []

    # 1. A check that could not be performed. Ranked first: a gap the platform
    #    created is more urgent than one the customer left, because the person
    #    reading this had no way to know about it.
    for node in run.nodes:
        if node.execution_kind != "external":
            continue
        output = node.output_dict()
        status = output.get("status")
        if node.status != "failed" and status not in ("error", "denied"):
            continue
        cause = compact_text(node.error) or compact_text(output.get("error")) or "The system did not respond"
        detail = f"{sentence(cause)} No changes were made."
        actions = [action for action in (factory.recheck(label="Retry"), factory.add_note()) if action]
        items.append(
            BusinessAttentionItem(
                id=f"attention:check:{node.node_id}",
                title=f"Could not check {humanize_identifier(node.node_id).lower()}",
                detail=detail,
                severity="warning",
                status_label="Not checked",
                actions=actions + [factory.technical_details("enrich")],
            )
        )

    # 2. Ambiguity a rule detected — several records match, and picking one
    #    automatically is precisely the decision a person should make.
    for node in run.nodes:
        if node.execution_kind != "external" or not node.succeeded:
            continue
        count = node.output_dict().get("count") or 0
        if isinstance(count, int) and count > 1:
            items.append(
                BusinessAttentionItem(
                    id=f"attention:ambiguous:{node.node_id}",
                    title="Several customer accounts match",
                    detail=f"{count} accounts match the name in this message. Owner, credit position and order history differ between them.",
                    severity="warning",
                    status_label="Needs a choice",
                    actions=[
                        action
                        for action in (factory.add_note(), factory.technical_details("enrich"))
                        if action
                    ],
                )
            )
            break

    # 3. What the customer did not tell us.
    known_fields = {key for key in payload if not isinstance(payload[key], dict)}
    for text in _missing_information(run, payload):
        field = _field_for(text, known_fields)
        if field and field not in _FIELDLESS_GAPS and not is_empty_value(payload.get(field)):
            # The extraction listed it as missing but a later correction or a
            # second pass filled it in. Reporting it now would be wrong.
            continue
        actions, status_label = _resolution_actions(
            text=text, field=field, factory=factory,
            attachments=attachments, records=records, editable=editable,
        )
        title = _title_for(text, field)
        items.append(
            BusinessAttentionItem(
                id=f"attention:missing:{field or re.sub(r'[^a-z0-9]+', '_', text.lower())[:40]}",
                title=title,
                detail=sentence(text) if title.lower() not in text.lower() else None,
                # A gap in a field the process itself branches on affected — or
                # could still affect — how this case is handled. One that no
                # rule reads is worth knowing about, but it changed nothing.
                severity="warning" if field and field in (rule_linked or set()) else "info",
                status_label=status_label,
                field=field,
                actions=actions,
            )
        )

    # 4. Determinations made before a fact was corrected.
    if run.stale_decisions:
        edited = ", ".join(
            sorted({field_label(str(edit.get("field"))) for edit in run.fact_edits if edit.get("field")})
        )
        items.append(
            BusinessAttentionItem(
                id="attention:stale",
                title="Some determinations may be out of date",
                detail=sentence(
                    f"{edited or 'A fact'} was corrected after these were worked out, so they were "
                    "made from a value that has since changed"
                ),
                severity="warning",
                status_label="Recheck recommended",
                actions=[action for action in (factory.recheck(label="Recheck now"),) if action],
            )
        )

    order = {"blocking": 0, "warning": 1, "info": 2}
    return sorted(
        items,
        key=lambda item: (
            order[item.severity],
            # Within a severity, put the ones somebody can act on now first.
            0 if (best_action(item) and best_action(item).type in _EVIDENCE_ACTIONS) else 1,
        ),
    )


def blocking_items(items: list[BusinessAttentionItem]) -> list[BusinessAttentionItem]:
    return [item for item in items if item.severity == "blocking"]
