"""Atomic read tools for the fixture-backed D365 Finance & SCM connector.

The tools accept references extracted from the customer message and return
matching Finance/SCM records. They do not assemble or interpret higher-level
workflow state.
"""
from __future__ import annotations

from typing import Any


def _string(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _collection(key: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            key: {"type": "array"},
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
        },
        "required": [key, "count", "truncated"],
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "find_customer",
        "title": "Find Customer",
        "description": "Find Finance & Operations customer records by customer or company name.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": _string("Customer name extracted from the message.", maxLength=200),
                "company_name": _string("Company name extracted from the message.", maxLength=200),
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 5},
            },
            "required": [],
            "anyOf": [{"required": ["customer_name"]}, {"required": ["company_name"]}],
        },
        "output_schema": _collection("customers"),
    },
    {
        "name": "find_quote",
        "title": "Find Quotation",
        "description": "Find a quotation using a quotation number or purchase-order number.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "quotation_number": _string("Quotation number extracted from the message.", maxLength=100),
                "purchase_order_number": _string("Purchase-order number extracted from the message.", maxLength=100),
                "account_id": _string(
                    "Confirmed customer account id, when known — narrows results to that "
                    "customer's own quotes rather than matching a quote number that happens "
                    "to belong to someone else.",
                    maxLength=100,
                ),
            },
            "required": [],
            "anyOf": [{"required": ["quotation_number"]}, {"required": ["purchase_order_number"]}],
        },
        "output_schema": _collection("quotes"),
    },
    {
        "name": "find_account_ownership",
        "title": "Find Account Ownership",
        "description": "Resolve a confirmed account's assignable ownership (sales, service, application) by role.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Confirmed customer account id.", maxLength=100),
            },
            "required": ["account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"ownership": {"type": "object"}},
            "required": ["ownership"],
        },
    },
    {
        "name": "find_credit_status",
        "title": "Find Credit Status",
        "description": "Find a confirmed account's credit-hold status.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Confirmed customer account id.", maxLength=100),
            },
            "required": ["account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"credit": {"type": "object"}},
            "required": ["credit"],
        },
    },
    {
        "name": "find_order_fulfilment_status",
        "title": "Find Order Fulfilment Status",
        "description": "Find why a sales order is where it is, using an order number or purchase-order number.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_number": _string("Sales-order number extracted from the message.", maxLength=100),
                "purchase_order_number": _string("Customer purchase-order number extracted from the message.", maxLength=100),
            },
            "required": [],
            "anyOf": [{"required": ["order_number"]}, {"required": ["purchase_order_number"]}],
        },
        "output_schema": {
            "type": "object",
            "properties": {"fulfilment": {"type": "object"}},
            "required": ["fulfilment"],
        },
    },
    {
        "name": "find_sales_order",
        "title": "Find Sales Order",
        "description": "Find a sales order using an order number or customer purchase-order number.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_number": _string("Sales-order number extracted from the message.", maxLength=100),
                "purchase_order_number": _string("Customer purchase-order number extracted from the message.", maxLength=100),
            },
            "required": [],
            "anyOf": [{"required": ["order_number"]}, {"required": ["purchase_order_number"]}],
        },
        "output_schema": _collection("salesorders"),
    },
    {
        "name": "find_shipment",
        "title": "Find Shipment",
        "description": "Find shipment records using shipment, order, or purchase-order references.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "shipment_number": _string("Shipment number extracted from the message.", maxLength=100),
                "order_number": _string("Sales-order number extracted from the message.", maxLength=100),
                "purchase_order_number": _string("Purchase-order number extracted from the message.", maxLength=100),
            },
            "required": [],
            "anyOf": [
                {"required": ["shipment_number"]},
                {"required": ["order_number"]},
                {"required": ["purchase_order_number"]},
            ],
        },
        "output_schema": _collection("shipments"),
    },
    {
        "name": "find_invoice",
        "title": "Find Invoice",
        "description": "Find an invoice using invoice, order, or purchase-order references.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_number": _string("Invoice number extracted from the message.", maxLength=100),
                "order_number": _string("Sales-order number extracted from the message.", maxLength=100),
                "purchase_order_number": _string("Purchase-order number extracted from the message.", maxLength=100),
            },
            "required": [],
            "anyOf": [
                {"required": ["invoice_number"]},
                {"required": ["order_number"]},
                {"required": ["purchase_order_number"]},
            ],
        },
        "output_schema": _collection("invoices"),
    },
    {
        "name": "find_contract",
        "title": "Find Contract",
        "description": "Find a customer contract by contract number.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_number": _string("Contract number extracted from the message.", maxLength=100),
            },
            "required": ["contract_number"],
        },
        "output_schema": _collection("contracts"),
    },
    {
        "name": "find_installed_unit",
        "title": "Find Installed Unit",
        "description": "Find installed equipment using serial number, model, manufacturer, or site.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "serial_number": _string("Equipment serial number.", maxLength=100),
                "existing_pump_model": _string("Existing pump model.", maxLength=200),
                "existing_pump_manufacturer": _string("Existing pump manufacturer.", maxLength=200),
                "site_or_location": _string("Installation site or location.", maxLength=200),
                "account_id": _string(
                    "Confirmed customer account id, when known — a serial number belonging "
                    "to a different account is reported as a mismatch rather than returned.",
                    maxLength=100,
                ),
            },
            "required": [],
            "anyOf": [
                {"required": ["serial_number"]},
                {"required": ["existing_pump_model"]},
                {"required": ["site_or_location"]},
            ],
        },
        "output_schema": _collection("installedunits"),
    },
    {
        "name": "find_inventory_availability",
        "title": "Find Inventory Availability",
        "description": "Find availability and lead-time records for a referenced pump model.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": _string("Product name extracted from the message.", maxLength=200),
                "pump_model": _string("Pump model extracted from the message.", maxLength=200),
                "product_family": _string("Product family extracted from the message.", maxLength=200),
                "quantity": {"type": "number", "description": "Requested quantity, when stated."},
            },
            "required": [],
            "anyOf": [
                {"required": ["product_name"]},
                {"required": ["pump_model"]},
                {"required": ["product_family"]},
            ],
        },
        "output_schema": _collection("inventory"),
    },
    {
        "name": "find_products",
        "title": "Find Products",
        "description": "Find pump catalogue records using product references supplied by the workflow.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": _string("Product name extracted from the message.", maxLength=200),
                "pump_model": _string("Pump model extracted from the message.", maxLength=200),
                "product_family": _string("Product family extracted from the message.", maxLength=200),
            },
            "required": [],
        },
        "output_schema": _collection("products"),
    },
]


TOOLS_BY_NAME = {definition["name"]: definition for definition in TOOL_DEFINITIONS}
READ_ONLY_TOOLS = tuple(definition["name"] for definition in TOOL_DEFINITIONS)