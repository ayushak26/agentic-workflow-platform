"""Tool definitions for the business-records MySQL server.

Nine tools, each a real parameterized query against a specific table, plus
one narrowly-scoped exception (`query_readonly`, for a lookup the other nine
don't cover) — never a raw, unclassified SQL executor: that one tool is
read-only by construction (a SELECT-only credential, a read-only
transaction, a row limit, a timeout — see app/mcp/business_records/
sql_guard.py), not merely by convention. Every tool declares its object
type, its record-identifier argument, and (for writes) exactly which fields
it may modify, directly in its schema/description rather than leaving that
to be inferred at runtime.
"""
from __future__ import annotations

from typing import Any


def _string(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _collection(key: str, item_schema: dict[str, Any]) -> dict[str, Any]:
    """Every collection-returning tool shares this envelope.

    `item_schema` is a real per-row object schema (not a bare `array`) so a
    downstream step can map a specific field — `account_id`, `pump_model` —
    instead of only ever seeing `data` as one opaque blob. Mirrors
    app/mcp/dynamics/tools.py's own `_collection(item_schema, key)` — this
    module had the looser, untyped version before; bringing it up to the
    same bar is what makes the Builder's field picker actually useful here.
    """
    return {
        "type": "object",
        "properties": {
            key: {"type": "array", "items": item_schema},
            "count": {"type": "integer", "description": "Number of records returned."},
            "truncated": {
                "type": "boolean",
                "description": "True when more records exist than the row limit returned.",
            },
        },
        "required": [key, "count", "truncated"],
    }


def _customer_item() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": _string("Customer/account id, in its source system."),
            "name": _string("Customer or company name."),
            "source": {
                "type": "string",
                "enum": ["d365f", "crm"],
                "description": "Which system this record came from.",
            },
            "region": _string("Sales region or country, when known."),
            "key_account": {
                "type": ["boolean", "null"],
                "description": "Whether this is a designated key account (Finance & Operations only).",
            },
            "credit_hold": {
                "type": ["boolean", "null"],
                "description": "Whether the account is on credit hold (Finance & Operations only).",
            },
        },
        "required": ["id", "name", "source"],
    }


def _order_item() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "order_number": _string("Our own sales-order number."),
            "purchase_order_number": _string("Customer's purchase-order number, when known."),
            "source": {"type": "string", "enum": ["d365f", "crm"]},
            "status": _string("Order or fulfilment status."),
            "delivery_status": _string("Delivery status, when known."),
        },
        "required": ["order_number", "source", "status"],
    }


def _inventory_item() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "pump_model": _string("Pump model."),
            "availability_status": _string("Stock availability, e.g. FEASIBLE."),
            "lead_time_days": {
                "type": ["integer", "null"],
                "description": "Lead time in days, when a specific figure is known.",
            },
        },
        "required": ["pump_model", "availability_status"],
    }


def _product_item() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "product_name": _string("Product name."),
            "pump_model": _string("Pump model, when this product is a pump."),
            "product_family": _string("Product family."),
            "manufacturer": _string("Manufacturer, when known (Finance & Operations only)."),
            "source": {"type": "string", "enum": ["d365f", "crm"]},
            "specs": {
                "type": "object",
                "description": "Extended technical attributes (Finance & Operations only).",
            },
        },
        "required": ["product_name", "pump_model", "product_family", "source"],
    }


def _case_record() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "case_id": _string("Generated case id."),
            "service_case_number": _string("Human-readable case number, e.g. CASE-0001."),
            "title": _string("Case summary."),
            "status": _string("Case status, e.g. open/in_progress/resolved."),
            "priority": _string("Case priority: low/normal/high."),
            "serial_number": {
                "type": ["string", "null"],
                "description": "Equipment serial number this case concerns, when set.",
            },
            "account_id": _string("Account this case belongs to."),
        },
        "required": ["service_case_number", "status", "account_id"],
    }


def _opportunity_record() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "opportunity_id": _string("Generated opportunity id."),
            "name": _string("Opportunity name."),
            "estimated_value": {
                "type": ["number", "null"],
                "description": "Estimated deal value, when known.",
            },
            "account_id": _string("Account this opportunity belongs to."),
        },
        "required": ["opportunity_id", "name", "account_id"],
    }


def _order_record() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "order_id": _string("Generated sales-order id."),
            "order_number": _string("Sales-order number."),
            "purchase_order_number": {
                "type": ["string", "null"],
                "description": "Customer purchase-order number, when quoted.",
            },
            "name": {"type": ["string", "null"], "description": "Order name/description."},
            "status": _string("Order status, e.g. draft/confirmed/fulfilled/cancelled."),
            "total_amount": {"type": ["number", "null"], "description": "Order total, when known."},
            "account_id": _string("Account this order belongs to."),
        },
        "required": ["order_id", "order_number", "status", "account_id"],
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # ---------------------------------------------------------------- READ --
    {
        "name": "customer_search",
        "title": "Customer Search",
        "description": (
            "Find customer/account records by name across the Finance & "
            "Supply Chain customer master and the CRM account list."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": _string("Customer or company name to search for.", maxLength=200),
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 5},
            },
            "required": ["customer_name"],
        },
        "output_schema": _collection("customers", _customer_item()),
        "typical_uses": [
            "Look up a customer or account by name",
            "Confirm a customer exists before creating a case or order",
        ],
    },
    {
        "name": "order_search",
        "title": "Order Search",
        "description": (
            "Find a sales order by our own order number or the customer's "
            "purchase-order number, across both the Finance & Supply Chain "
            "and CRM order systems."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_number": _string("Sales-order number.", maxLength=100),
                "purchase_order_number": _string("Customer purchase-order number.", maxLength=100),
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 5},
            },
            "required": [],
            "anyOf": [{"required": ["order_number"]}, {"required": ["purchase_order_number"]}],
        },
        "output_schema": _collection("orders", _order_item()),
        "typical_uses": [
            "Find a sales order by order number or purchase-order number",
            "Check an order's fulfilment or delivery status",
        ],
    },
    {
        "name": "inventory_check",
        "title": "Inventory Check",
        "description": "Check stock availability and lead time for a pump model.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "pump_model": _string("Pump model to check availability for.", maxLength=128),
            },
            "required": ["pump_model"],
        },
        "output_schema": _collection("inventory", _inventory_item()),
        "typical_uses": [
            "Check stock availability for a pump model",
            "Estimate lead time before promising a delivery date",
        ],
    },
    {
        "name": "product_search",
        "title": "Product Search",
        "description": (
            "Find pump catalogue records by product name or family, across "
            "the Finance & Supply Chain and CRM product catalogues."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": _string("Product name to search for.", maxLength=200),
                "product_family": _string("Product family to search for.", maxLength=128),
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
            },
            "required": [],
            "anyOf": [{"required": ["product_name"]}, {"required": ["product_family"]}],
        },
        "output_schema": _collection("products", _product_item()),
        "typical_uses": [
            "Look up a pump's specs by product name or family",
            "Identify a replacement or comparable product",
        ],
    },
    {
        "name": "query_readonly",
        "title": "Read-Only SQL Query",
        "description": (
            "Run a read-only SQL SELECT against the business-records "
            "database, for a lookup the other narrow tools above don't "
            "cover. Runs under a SELECT-only credential, in a read-only "
            "transaction, with a row limit and a hard timeout — never a "
            "write, never more than one statement."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": _string(
                    "A single SELECT statement. Use %(name)s placeholders "
                    "for any value from params — never build the string "
                    "with the value already inside it.",
                    maxLength=4000,
                ),
                "params": {
                    "type": "object",
                    "description": "Named values for the query's %(name)s placeholders.",
                    "additionalProperties": True,
                },
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 30, "default": 10},
            },
            "required": ["sql"],
        },
        "output_schema": _collection("rows", {"type": "object", "additionalProperties": True}),
        "typical_uses": [
            "Run a one-off lookup none of the other tools cover",
        ],
    },
    # --------------------------------------------------------------- WRITE --
    {
        "name": "create_case",
        "title": "Create Support Case",
        "description": "Create a new CRM service case for a known account.",
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Confirmed CRM account id this case belongs to.", maxLength=64),
                "title": _string("One-line summary of the case.", maxLength=255),
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "default": "normal",
                    "description": "Case priority.",
                },
                "serial_number": _string("Equipment serial number this case concerns, if any.", maxLength=64),
            },
            "required": ["account_id", "title"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"case": _case_record()},
            "required": ["case"],
        },
        "typical_uses": [
            "Open a support case for a known account",
        ],
    },
    {
        "name": "create_opportunity",
        "title": "Create Opportunity",
        "description": "Create a new CRM sales opportunity for a known account.",
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Confirmed CRM account id this opportunity belongs to.", maxLength=64),
                "name": _string("Opportunity name.", maxLength=255),
                "estimated_value": {"type": "number", "description": "Estimated deal value, when known."},
                "estimated_close_date": _string("Estimated close date, ISO 8601 (YYYY-MM-DD).", maxLength=10),
            },
            "required": ["account_id", "name"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"opportunity": _opportunity_record()},
            "required": ["opportunity"],
        },
        "typical_uses": [
            "Log a new sales opportunity for a known account",
        ],
    },
    {
        "name": "create_order",
        "title": "Create Sales Order",
        "description": "Create a new CRM sales order for a known account.",
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Confirmed CRM account id this order belongs to.", maxLength=64),
                "order_number": _string("New sales-order number.", maxLength=64),
                "name": _string("Order name/description.", maxLength=255),
                "purchase_order_number": _string("Customer purchase-order number, if quoted.", maxLength=64),
                "total_amount": {"type": "number", "description": "Order total, when known."},
            },
            "required": ["account_id", "order_number"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"order": _order_record()},
            "required": ["order"],
        },
        "typical_uses": [
            "Place a new sales order for a known account",
        ],
    },
    {
        "name": "update_order",
        "title": "Update Sales Order",
        "description": (
            "Update an existing CRM sales order's status or total. Object: "
            "sales_order. Record identifier: order_number. Modifiable "
            "fields: status, total_amount."
        ),
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_number": _string("The sales order to update.", maxLength=64),
                "status": _string("New order status, e.g. confirmed/fulfilled/cancelled.", maxLength=32),
                "total_amount": {"type": "number", "description": "Corrected order total, when changing."},
            },
            "required": ["order_number"],
            "anyOf": [{"required": ["status"]}, {"required": ["total_amount"]}],
        },
        "output_schema": {
            "type": "object",
            "properties": {"order": _order_record(), "updated": {"type": "boolean"}},
            "required": ["order", "updated"],
        },
        "typical_uses": [
            "Confirm or update the status of an existing sales order",
        ],
    },
    {
        "name": "update_case",
        "title": "Update Support Case",
        "description": (
            "Update an existing CRM service case's status or priority. "
            "Object: service_case. Record identifier: service_case_number. "
            "Modifiable fields: status, priority."
        ),
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_case_number": _string("The case to update.", maxLength=64),
                "status": _string("New case status, e.g. open/in_progress/resolved.", maxLength=32),
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "New case priority.",
                },
            },
            "required": ["service_case_number"],
            "anyOf": [{"required": ["status"]}, {"required": ["priority"]}],
        },
        "output_schema": {
            "type": "object",
            "properties": {"case": _case_record(), "updated": {"type": "boolean"}},
            "required": ["case", "updated"],
        },
        "typical_uses": [
            "Resolve or reprioritize an existing support case",
        ],
    },
]

TOOLS_BY_NAME = {definition["name"]: definition for definition in TOOL_DEFINITIONS}
READ_ONLY_TOOLS = tuple(
    definition["name"] for definition in TOOL_DEFINITIONS if definition["operation"] == "read"
)
