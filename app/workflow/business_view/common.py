"""Shared, dependency-free helpers for the Business View projection.

Everything here is pure formatting or classification. No I/O, no model calls,
no run mutation — so every downstream module stays testable with plain dicts.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

#: Terms the workflow vocabulary uses for "no value", which a business user
#: should see as "Not stated" rather than as a literal token. The extraction
#: prompt in the pump/triage workflows instructs the model to emit exactly
#: these for absent facts, so they are contract, not guesswork.
_EMPTY_TOKENS = {"", "unknown", "none", "n/a", "na", "null", "other", "-", "—"}

#: Keys carried by a structured extraction that are machinery rather than
#: business facts. They are used elsewhere in the projection (summary,
#: confidence, attention) and would only add noise to "What I understood".
UNDERSTANDING_INTERNAL_KEYS = {
    "raw",
    "parsed",
    "confidence",
    "missing_information",
    "english_summary",
    "summary",
    "language",
}

#: Preferred display order for the business fields these customer-operations
#: workflows extract. Anything not listed keeps its natural order after these,
#: so a new extraction field appears without a code change — just lower down.
UNDERSTANDING_FIELD_ORDER = [
    "customer_name",
    "contact_name",
    "intent",
    "primary_intent",
    "request_types",
    "secondary_intents",
    "requested_action",
    "requested_quantity",
    "pump_model",
    "product_model",
    "serial_number",
    "sales_order_reference",
    "quotation_reference",
    "customer_po_reference",
    "secondary_reference",
    "requested_delivery_date",
    "lifecycle_stage",
    "technical_complexity",
    "complaint_type",
    "requested_document_type",
    "has_safety_issue",
    "production_stoppage",
    "technical_deviation_requested",
    "price_or_terms_changed",
    "technical_change_requested",
    "invoice_reference",
    "requires_action",
]

#: Business-language labels for field names whose humanised form would be
#: technically correct but commercially odd ("Has safety issue" → "Safety issue").
FIELD_LABELS = {
    "customer_name": "Customer",
    "contact_name": "Contact",
    "intent": "Request",
    "request_types": "Request",
    "secondary_intents": "Additional requests",
    "secondary_reference": "Reference for the additional request",
    "requested_action": "Requested action",
    "requested_quantity": "Quantity",
    "pump_model": "Pump model",
    "product_model": "Product model",
    "serial_number": "Serial number",
    "sales_order_reference": "Sales order",
    "quotation_reference": "Quotation",
    "customer_po_reference": "Customer PO",
    "requested_delivery_date": "Requested delivery date",
    "lifecycle_stage": "Lifecycle",
    "technical_complexity": "Complexity",
    "complaint_type": "Complaint type",
    "requested_document_type": "Requested document",
    "has_safety_issue": "Safety issue",
    "production_stoppage": "Production stoppage",
    "technical_deviation_requested": "Technical deviation requested",
    "price_or_terms_changed": "Commercial terms change requested",
    "order_reference_present": "Order reference given",
    "serial_number_present": "Serial number given",
    "account_id": "Account",
    "account_name": "Customer account",
    "sales_region": "Region",
    "key_account": "Key account",
    "credit_hold": "Credit hold",
    "availability_status": "Availability",
    "fulfilment_status": "Fulfilment",
    "delivery_status": "Delivery",
    "order_status": "Order status",
    "warranty_active": "Warranty active",
    "human_review": "Human review",
    "escalation_reason": "Escalation reason",
    "primary_intent": "Request",
    "complexity": "Complexity",
    "urgency": "Urgency",
    "product_resolved": "Product identified",
    "technical_specifications": "Technical specifications",
    "spare_parts": "Spare parts required",
}

#: Enumerated values these workflows emit, in business words. Anything absent
#: falls through to humanize_identifier, which handles the general case.
VALUE_LABELS = {
    "RFQ": "Quotation request",
    "NEW_ORDER": "New order",
    "ORDER_STATUS": "Order status enquiry",
    "ORDER_CHANGE": "Order change",
    "TECHNICAL_SUPPORT": "Technical support",
    "COMPLAINT": "Complaint",
    "SPARE_PARTS": "Spare parts",
    "DOCUMENT_REQUEST": "Document request",
    "INVOICE": "Invoice",
    "PRODUCT_SELECTION": "Product selection",
    "DELIVERY_QUERY": "Delivery enquiry",
    "PRODUCT_COMPLAINT": "Product complaint",
    "DELIVERY_COMPLAINT": "Delivery complaint",
    "INVOICE_QUERY": "Invoice query",
    "ACCOUNT_QUERY": "Account query",
    "GENERAL_ENQUIRY": "General enquiry",
    "presales": "Presales",
    "order_execution": "Order execution",
    "installed_base": "Installed base",
    "standard": "Standard",
    "technical": "Technical",
    "complex": "Complex",
}


def humanize_identifier(value: str) -> str:
    """`rfq_inside_sales_queue` → `Rfq inside sales queue`.

    Kept byte-compatible with the frontend's original helper (and the previous
    projection) so labels do not shift for workflows that rely on it.
    """
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    spaced = re.sub(r"[_./-]+", " ", spaced)
    spaced = re.sub(r"\b(agent|node)\b", "", spaced, flags=re.I)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    if not spaced:
        return "Workflow step"
    return spaced[0].upper() + spaced[1:]


def field_label(key: str) -> str:
    """Business label for an extraction/record field name.

    A trailing `_name` is dropped ("territory_sales_owner_name" → "Territory
    sales owner"): the field holds a name, but the *fact* is the owner.
    """
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    trimmed = re.sub(r"_(name|reference|status|id)$", "", key) if "_" in key else key
    return FIELD_LABELS.get(trimmed) or humanize_identifier(trimmed or key)


#: Verbs a lookup node id starts with. Stripped so "find_customer" reads as
#: "Customer" — the business names the thing checked, not the checking.
_LOOKUP_VERB = re.compile(r"^(find|get|fetch|lookup|look_up|read|check|search|retrieve)_", re.I)


def lookup_label(node_id: str) -> str:
    """`get_sales_order` → `Sales order`; `emergency_customer_lookup` → `Emergency customer`."""
    trimmed = _LOOKUP_VERB.sub("", node_id)
    trimmed = re.sub(r"_(lookup|search|check)$", "", trimmed, flags=re.I)
    return humanize_identifier(trimmed or node_id)


#: Short tokens that are acronyms in this domain and must keep their case.
_ACRONYMS = {"RFQ", "PO", "SO", "ERP", "CRM", "KAM", "GA", "EHS", "QHSE", "ATEX", "OTC"}


def humanize_route(value: str) -> str:
    """`INSIDE_SALES_QUEUE` → `Inside sales queue`; `RFQ` stays `RFQ`.

    Route names are written as constants because they label graph edges. A
    business user reading "CONTINUE" learns nothing that "Continue" does not
    say more quietly.
    """
    return humanize_case_title(re.sub(r"[_./-]+", " ", value or ""))


def humanize_case_title(value: str) -> str:
    """`STANDARD RFQ - KEY ACCOUNT` → `Standard RFQ — key account`.

    Underscored case types (`NEW_EQUIPMENT_ENQUIRY`) are word-separated first,
    so a workflow that names its case types as machine tokens still reads as a
    sentence on the card.
    """
    words = re.split(r"\s+", (value or "").replace("_", " ").strip())
    if not words or not words[0]:
        return ""
    out = []
    for word in words:
        bare = word.strip("-—:,.")
        if bare.upper() in _ACRONYMS:
            out.append(word.replace(bare, bare.upper()))
        elif word in ("-", "—"):
            out.append("—")
        else:
            out.append(word.lower())
    text = " ".join(out).strip()
    return text[0].upper() + text[1:] if text else ""


def is_empty_value(value: Any) -> bool:
    """Whether a value means "the workflow found nothing here".

    Booleans are never empty — `False` is a real commercial answer ("no safety
    issue"), and treating it as missing would hide the most reassuring fact on
    the screen.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_TOKENS
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def format_value(value: Any, *, key: str = "") -> str:  # noqa: C901
    """A business-readable rendering of one extracted value.

    Never emits JSON: a dict collapses to its own readable key/value pairs, and
    anything genuinely unrenderable becomes a count. A screen that prints
    `{"a": 1}` at a salesperson has already failed.
    """
    if is_empty_value(value):
        # An empty collection is "none of them", which is a different (and
        # more reassuring) statement than "nobody told us".
        return "None" if isinstance(value, (list, tuple, set)) else "Not stated"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        if "confidence" in key and 0.0 <= float(value) <= 1.0:
            return f"{round(float(value) * 100)}%"
        return f"{value:,}" if isinstance(value, int) else f"{value:g}"
    if isinstance(value, str):
        text = value.strip()
        return VALUE_LABELS.get(text) or (text if key else text)
    if isinstance(value, (list, tuple, set)):
        parts = [format_value(item) for item in value if not is_empty_value(item)]
        return ", ".join(parts) if parts else "Not stated"
    if isinstance(value, dict):
        parts = [
            f"{field_label(str(k))}: {format_value(v)}"
            for k, v in list(value.items())[:4]
            if not is_empty_value(v)
        ]
        return "; ".join(parts) if parts else "Not stated"
    return str(value)


def compact_text(value: Any, max_length: int = 220) -> str | None:
    """Whitespace-collapsed, length-capped text, or None for anything else."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    return text if len(text) <= max_length else f"{text[: max_length - 1]}…"


def timestamp_iso(value: Any) -> str | None:
    """Normalise the several time shapes run history stores into one ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        return value
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def duration_ms(started: Any, ended: Any) -> int | None:
    """Elapsed milliseconds between two epoch-second timestamps, if both exist."""
    try:
        if started is None or ended is None:
            return None
        delta = float(ended) - float(started)
    except (TypeError, ValueError):
        return None
    return max(0, int(delta * 1000))


def sentence(text: str) -> str:
    """Capitalise and full-stop a fragment so headlines read as prose."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned if cleaned[-1] in ".!?" else f"{cleaned}."


#: Lines a handoff note starts with, e.g. "Primary team: Inside Sales".
_HANDOFF_LINE = re.compile(r"^\s{0,4}([A-Z][A-Za-z0-9 /\-()]{2,40}):\s*(.+?)\s*$")
_CASE_HEADER = re.compile(r"^\s*CASE:\s*(.+?)\s*$|^\s*PRIORITY:\s*(.+?)\s*$")

#: Handoff-note keys the projection understands, mapped to a stable slot.
_HANDOFF_KEYS = {
    "primary team": "team",
    "team": "team",
    "primary owner": "owner",
    "primary technical owner": "owner",
    "commercial owner": "commercial_owner",
    "commercial visibility": "commercial_owner",
    "customer-facing coordinator": "coordinator",
    "supporting team": "supporting_team",
    "supporting teams": "supporting_team",
    "second team required": "supporting_team",
    "support": "supporting_team",
    "reason": "reason",
    "action": "action",
    "customer": "customer",
    "region": "region",
    "order": "order",
    "unit": "unit",
    "site": "site",
    "contact": "contact",
    "summary": "summary",
    "delivery status": "delivery_status",
    "order status": "order_status",
    "fulfilment status": "fulfilment_status",
    "availability status": "availability_status",
}


def parse_handoff_note(text: Any) -> dict[str, str]:
    """Read the `CASE: …` / `Key: value` handoff note these workflows emit.

    The customer-operations workflows end every branch in a DataTransformAgent
    that formats a short note for the receiving team: a `CASE:`/`PRIORITY:`
    header followed by `Primary team:`, `Reason:`, `Action:` lines. That note
    is the workflow author's own statement of who owns the case and what they
    should do — by far the best available source for the Business View's
    decision and next-step cards.

    This reads only leading `Key: value` lines and a header, ignores prose
    paragraphs, and returns `{}` for anything that is not such a note. It never
    infers a team that is not written down.
    """
    if not isinstance(text, str) or not text.strip():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            # A blank line ends the header block; the rest is prose meant for
            # a person to read, not key/value metadata.
            if parsed:
                break
            continue
        header = _CASE_HEADER.match(line)
        if header and "case_title" not in parsed:
            parsed["case_title"] = (header.group(1) or header.group(2) or "").strip()
            continue
        match = _HANDOFF_LINE.match(line)
        if not match:
            if parsed:
                break
            continue
        key = _HANDOFF_KEYS.get(match.group(1).strip().lower())
        value = match.group(2).strip()
        if key and value and not is_empty_value(value) and key not in parsed:
            parsed[key] = value
    return parsed


def title_case_team(value: str) -> str:
    """Trim a handoff team string to the part a status headline can carry.

    "Inside Sales / Customer Support" → "Inside Sales": a status line names one
    owner. The full string stays available on the decision card.
    """
    head = value.split("/")[0].split("(")[0].strip(" .")
    return head or value.strip()
