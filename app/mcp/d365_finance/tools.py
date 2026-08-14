"""Tool vocabulary for the fixture-backed d365-finance-scm-mcp business layer.

Matches the seven tools named in workflows/pump_manufacturer_case_routing.yaml's
business_context config and mcp-servers/d365-finance-scm-mcp/README.md's
"Recommended business layer for your assessment" section.
"""
from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "find_customer",
        "description": "Find Finance & Operations customers by name.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Customer name to search for."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 5},
            },
            "required": ["customer_name"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "customers": {"type": "array"},
                "count": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
    },
    {
        "name": "get_account_ownership",
        "description": "Get the named commercial/technical owners for a customer account.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"ownership": {"type": "object"}},
        },
    },
    {
        "name": "get_credit_status",
        "description": "Get whether a customer account is on credit hold.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"credit": {"type": "object"}},
        },
    },
    {
        "name": "get_quote",
        "description": (
            "Look up a quotation by reference, confirm it belongs to the given customer, "
            "and check whether a customer PO matches it."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "quotation_reference": {"type": "string"},
                # Required: a quotation reference alone is not proof the quote
                # belongs to whoever sent the message. Without the account to
                # check it against, one customer's PO would validate cleanly
                # against another customer's quote.
                "account_id": {"type": "string"},
                "customer_po_reference": {"type": "string"},
            },
            "required": ["quotation_reference", "account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"quote": {"type": "object"}},
        },
    },
    {
        "name": "get_sales_order",
        "description": "Look up a sales order's status and whether production has started.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {"sales_order_reference": {"type": "string"}},
            "required": ["sales_order_reference"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"order": {"type": "object"}},
        },
    },
    {
        "name": "get_order_fulfilment_status",
        "description": "Look up a sales order's production/fulfilment and delivery status.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {"sales_order_reference": {"type": "string"}},
            "required": ["sales_order_reference"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"fulfilment": {"type": "object"}},
        },
    },
    {
        "name": "get_installed_unit",
        "description": (
            "Look up an installed pump by serial number, confirm it belongs to the given "
            "customer, and report its warranty position."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "serial_number": {"type": "string"},
                # Required for the same reason as get_quote's: a serial number
                # is not proof of ownership, and the answer must never describe
                # another customer's equipment.
                "account_id": {"type": "string"},
            },
            "required": ["serial_number", "account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"unit": {"type": "object"}},
        },
    },
    {
        "name": "get_inventory_availability",
        "description": "Look up delivery feasibility for a pump model.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {"pump_model": {"type": "string"}},
            "required": ["pump_model"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"availability": {"type": "object"}},
        },
    },
]

TOOL_BY_NAME = {definition["name"]: definition for definition in TOOL_DEFINITIONS}
