"""The BASF RFQ from the redesign brief, as a real run document.

Built against the real `workflows/pump_manufacturer_case_routing.yaml` spec —
no mocking of the workflow — so the projection tests exercise the actual
routers, MCP node shapes and handoff notes the platform produces. A change to
that workflow that breaks the business projection breaks these tests, which is
the point of not stubbing the spec.

The run below is the department-first Level 3 graph: understand → find the
customer → read the business context → decide the department → decide the
assignment → route. BASF's RFQ is standard and BASF Coatings' account has no
assignable owner, so it lands on the Inside Sales queue.
"""
from __future__ import annotations

import json
from typing import Any

from app.runtime.loader import load_workflow

PUMP_SPEC = load_workflow("workflows/pump_manufacturer_case_routing.yaml")
PUMP_NODE_TYPES = {node.id: node.type for node in PUMP_SPEC.nodes}

T0 = 1_700_000_000.0

BASF_PARSED: dict[str, Any] = {
    "language": "en",
    "english_summary": (
        "BASF requests a quotation for 5 new pumps based on an attached datasheet, "
        "plus spare parts for previous order SO 231706."
    ),
    "primary_intent": "RFQ",
    "secondary_intents": [],
    "secondary_reference": "",
    "requires_action": True,
    "customer_name": "BASF SE",
    "contact_name": "",
    "quotation_reference": "",
    "customer_po_reference": "",
    "sales_order_reference": "SO 231706",
    "invoice_reference": "",
    "pump_model": "",
    "serial_number": "",
    "requested_quantity": "5",
    "requested_delivery_date": "",
    "lifecycle_stage": "presales",
    "technical_complexity": "standard",
    "complaint_type": "none",
    "requested_document_type": "NONE",
    "price_or_terms_changed": False,
    "technical_change_requested": False,
    "order_reference_present": True,
    "serial_number_present": False,
    "confidence": 0.86,
    "missing_information": [
        "contact name",
        "pump model",
        "delivery date",
        "technical specifications from attached datasheet",
        "specific spare parts required",
    ],
}

#: Copied from a real run of the workflow (sales_queue's `text`), so the
#: fixture cannot drift from the note the workflow actually emits.
INSIDE_SALES_NOTE = (
    "CASE: NEW_EQUIPMENT_ENQUIRY\n"
    "Primary team: Inside Sales\n"
    "Department: SALES\n"
    "Reason: The customer is asking about equipment they have not yet bought.\n"
    "Action: Qualify the application and prepare a quotation.\n"
    "Summary: BASF requests a quotation for 5 new pumps.\n"
    "Owner on record:  (unassigned)\n"
    "Customer: BASF SE\n"
    "Contact: \n"
    "Order: SO 231706"
)

#: The routing rules that fired, as the DecisionAgent records them.
ROUTING_DECISIONS: dict[str, Any] = {
    "primary_department": "SALES",
    "sub_team": "Inside Sales",
    "assignment_track": "ACCOUNT",
    "case_type": "NEW_EQUIPMENT_ENQUIRY",
    "priority": "NORMAL",
    "requires_human": False,
    "support_track": "CASE",
    "service_track": "QUEUE",
    "other_track": "SPECIALIST",
    "reason": ["The customer is asking about equipment they have not yet bought."],
    "reason_line": "The customer is asking about equipment they have not yet bought.",
    "why_not": "",
    "next_action": "Qualify the application and prepare a quotation.",
}

BUSINESS_FACTS: dict[str, Any] = {
    "customer_state": "FOUND",
    "customer_verified": True,
    "ownership_state": "AVAILABLE",
    "order_state": "FOUND",
    "fulfilment_state": "UNKNOWN",
    "quote_state": "NO_REFERENCE",
    "serial_state": "ABSENT",
    "warranty_state": "UNKNOWN",
    "availability_state": "UNKNOWN",
    "order_exists": True,
    "serial_verified": False,
}

#: What the cost ledger recorded for the one AI call this run made. Note the
#: requested model differs from the executed one — the case §22–§24 exist for.
BASF_COST_ENTRIES = [
    {
        "node_id": "understand_message",
        "model": "claude-sonnet-4-5",
        "intended_model": "auto",
        "cost_usd": 0.0018,
        "latency_ms": 1400,
        "task_type": "structured_extraction",
        "provider": "anthropic",
        "fallback_used": False,
        "fallback_reason": None,
    }
]


def node_run(status: str, started: float, ended: float | None = None, **extra: Any) -> dict[str, Any]:
    return {"status": status, "started_at": started, "ended_at": ended, **extra}


def basf_run(**overrides: Any) -> dict[str, Any]:
    """A completed BASF RFQ routed to the Inside Sales queue."""
    run: dict[str, Any] = {
        "run_id": "run-basf-001",
        "session_id": "sess-1",
        "status": "completed",
        "workflow_name": "Pump Customer Routing — Level 3 · Production",
        "started_at": T0,
        "ended_at": T0 + 12,
        "updated_at": T0 + 12,
        "node_types": dict(PUMP_NODE_TYPES),
        "inputs": {"message": "Dear Sirs, we would like a quotation for five pumps…"},
        "node_runs": {
            "understand_message": node_run(
                "completed", T0, T0 + 4,
                model_selections=[{
                    "requested_model": "auto",
                    "actual_model": "claude-sonnet-4-5",
                    "fallback": False,
                    "reason": "structured extraction",
                }],
            ),
            "find_customer": node_run("completed", T0 + 4, T0 + 5),
            "customer_state_router": node_run("completed", T0 + 5, T0 + 5.1),
            "customer_confirmed": node_run("completed", T0 + 5.1, T0 + 5.2),
            "get_ownership": node_run("completed", T0 + 5.2, T0 + 6),
            "get_order": node_run("completed", T0 + 6, T0 + 7),
            "business_facts": node_run("completed", T0 + 7, T0 + 7.1),
            "routing_decision": node_run("completed", T0 + 7.1, T0 + 7.3),
            "assignment_decision": node_run("completed", T0 + 7.3, T0 + 7.4),
            "multi_intent_router": node_run("completed", T0 + 7.4, T0 + 7.5),
            "primary_department_router": node_run("completed", T0 + 7.5, T0 + 7.6),
            "sales_owner_router": node_run("completed", T0 + 7.6, T0 + 7.7),
            "sales_queue": node_run("completed", T0 + 7.7, T0 + 8),
        },
        "outputs": {
            # `raw` is the model's own JSON string — exactly what the old
            # Business View printed, and what the projection must never carry.
            "understand_message": {"raw": json.dumps(BASF_PARSED), "parsed": dict(BASF_PARSED)},
            "find_customer": {
                "status": "ok", "found": True, "count": 1, "data": {},
                "first": {
                    "account_id": "ACC-1", "account_name": "BASF SE",
                    "sales_region": "DACH", "key_account": False,
                },
            },
            "customer_state_router": {
                "route": "CONTINUE", "reason": "no case matched; used the fallback branch",
                "used_fallback": True,
            },
            "customer_confirmed": {
                "data": {"account_id": "ACC-1", "account_name": "BASF SE", "match_count": 1},
                "defaulted": [],
            },
            "get_ownership": {
                "status": "ok", "found": True, "count": 1, "data": {},
                "first": {
                    "account_owner_name": "", "account_owner_recorded_name": "",
                    "account_owner_status": "unassigned", "account_owner_team": "",
                    "territory_sales_owner_name": "", "territory_sales_owner_recorded_name": "",
                    "territory_sales_owner_status": "unassigned", "territory_sales_owner_team": "",
                    "service_owner_name": "Hans Vogel", "service_owner_status": "active",
                },
            },
            "get_order": {
                "status": "ok", "found": True, "count": 1, "data": {},
                "first": {"sales_order_reference": "SO 231706", "order_status": "CLOSED"},
            },
            "business_facts": {
                "decisions": dict(BUSINESS_FACTS),
                "matched_rules": [
                    "The customer was identified",
                    "Ownership was readable",
                    "The order exists",
                ],
                "summary": [],
            },
            "routing_decision": {
                "decisions": dict(ROUTING_DECISIONS),
                "matched_rules": ["A new-equipment enquiry belongs to Sales"],
                "summary": [],
            },
            "assignment_decision": {
                "decisions": {
                    "owner_name": "", "owner_status": "unassigned",
                    "owner_recorded_name": "", "owner_team": "",
                },
                "matched_rules": ["Commercial cases go to the territory owner"],
                "summary": [],
            },
            "multi_intent_router": {
                "route": "SINGLE", "reason": "no case matched; used the fallback branch",
                "used_fallback": True,
            },
            "primary_department_router": {
                "route": "SALES",
                "reason": "routed on routing_decision.decisions.primary_department = 'SALES'",
                "route_value": "SALES",
            },
            "sales_owner_router": {
                "route": "QUEUE", "reason": "no case matched; used the fallback branch",
                "used_fallback": True,
            },
            "sales_queue": {
                # `text` is repeated at the top level because that is where
                # app/workflow/business_view reads a handoff note from; the
                # `data` block below is the node's real output shape.
                "text": INSIDE_SALES_NOTE,
                "customer_reply_draft": "Thank you for your message…",
                "data": {
                    "routing": {
                        "primary_department": "SALES", "sub_team": "Inside Sales",
                        "case_type": "NEW_EQUIPMENT_ENQUIRY", "priority": "NORMAL",
                    },
                    "assignment": {
                        "owner_name": "", "owner_status": "unassigned",
                        "owner_recorded_name": "", "owner_team": "",
                        "fallback_team": "Inside Sales",
                    },
                    "next_action": "Qualify the application and prepare a quotation.",
                    "requires_human": False,
                    "text": INSIDE_SALES_NOTE,
                    "customer_reply_draft": "Thank you for your message…",
                },
                "defaulted": [],
            },
        },
    }
    run.update(overrides)
    return run
