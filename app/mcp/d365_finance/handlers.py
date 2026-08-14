"""Fixture-backed handlers for the d365-finance-scm-mcp business-tool layer.

Each function mirrors one of the seven narrow tools mcp-servers/d365-finance-
scm-mcp's README recommends building over its generic erp_query/erp_get_record
adapter. Reuses app.mcp.dynamics's FixtureBackend/odata helpers — the query
semantics (contains/eq filters, $select projection) are OData conventions, not
CRM-specific, so there is no reason to re-implement them for this domain.
"""
from __future__ import annotations

from typing import Any

from app.mcp.dynamics import odata
from app.mcp.dynamics.client import DynamicsBackend


def _collection(rows: list[dict[str, Any]], key: str, limit: int) -> dict[str, Any]:
    truncated = len(rows) > limit
    return {key: rows[:limit], "count": min(len(rows), limit), "truncated": truncated}


def _limit(arguments: dict[str, Any], default: int, maximum: int) -> int:
    value = arguments.get("limit", default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


CUSTOMER_COLUMNS = [
    "customerid", "customer_account", "name", "data_area_id", "sales_region",
    "key_account", "account_owner_name", "territory_sales_owner_name",
    "sales_engineer_name", "application_specialist_name", "service_owner_name",
    "credit_hold",
]


def _customer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": row.get("customerid") or "",
        "account_name": row.get("name") or "",
        "customer_account": row.get("customer_account") or "",
        "sales_region": row.get("sales_region") or "",
        "key_account": bool(row.get("key_account", False)),
    }


async def find_customer(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    """Search customers by name — a customer writing 'Meridian' should find
    'Meridian Process Systems', same contains-or-exact convention as the CRM
    connector's find_account."""
    name = arguments.get("customer_name")
    limit = _limit(arguments, 5, 25)
    if not name:
        return _collection([], "customers", limit)
    expression = odata.any_of(
        odata.string_filter("name", name),
        odata.contains_filter("name", name),
    )
    rows = await backend.query(
        "customers", select=CUSTOMER_COLUMNS, filter_expression=expression,
        order_by="name asc", top=limit + 1,
    )
    return _collection([_customer(row) for row in rows], "customers", limit)


OWNER_ROLES = [
    "account_owner", "territory_sales_owner", "sales_engineer",
    "application_specialist", "service_owner",
]


async def get_account_ownership(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve account ownership to people who can actually be assigned today.

    A name on an account is not the same as an owner. Stale ownership is
    entirely ordinary in CRM data — people move team, go on long-term leave and
    leave the company faster than master data is corrected — and routing a case
    to a departed salesperson's mailbox loses it silently, which is worse than
    never having named an owner at all.

    So each `<role>_name` here is the *assignable* name: empty unless the
    directory says that person is active. The recorded name, the reason and the
    owning team travel alongside it, so a queue can explain why it received the
    case and hand it to the right team rather than the right individual.
    """
    account_id = arguments.get("account_id")
    if not account_id:
        return {}
    row = await backend.get("customers", account_id, select=CUSTOMER_COLUMNS)
    if row is None:
        return {}

    directory = {
        (employee.get("display_name") or "").strip().lower(): employee
        for employee in await backend.query(
            "employees",
            select=["employeeid", "display_name", "active", "team_name"],
            top=500,
        )
    }

    ownership: dict[str, Any] = {}
    for role in OWNER_ROLES:
        recorded = (row.get(f"{role}_name") or "").strip()
        employee = directory.get(recorded.lower()) if recorded else None
        if not recorded:
            status = "unassigned"
        elif employee is None:
            # On the account but not in the directory — treat as unassignable
            # rather than guessing they are still here.
            status = "not_in_directory"
        elif employee.get("active"):
            status = "active"
        else:
            status = "inactive"

        ownership[f"{role}_name"] = recorded if status == "active" else ""
        ownership[f"{role}_recorded_name"] = recorded
        ownership[f"{role}_status"] = status
        ownership[f"{role}_team"] = (employee or {}).get("team_name") or ""

    return {"ownership": ownership}


async def get_credit_status(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = arguments.get("account_id")
    if not account_id:
        return {}
    row = await backend.get("customers", account_id, select=["customerid", "credit_hold"])
    if row is None:
        return {}
    return {"credit": {"credit_hold": bool(row.get("credit_hold", False))}}


async def get_quote(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    quotation_reference = arguments.get("quotation_reference")
    if not quotation_reference:
        return {}
    rows = await backend.query(
        "quotes",
        select=["quoteid", "quotation_reference", "_customerid_value", "status",
                "linked_po_reference", "pump_model"],
        filter_expression=odata.string_filter("quotation_reference", quotation_reference),
        top=1,
    )
    if not rows:
        return {}
    row = rows[0]
    # A quotation reference is not proof of ownership. Quoting someone else's
    # quote number must never validate: confirm the quote belongs to the
    # account we resolved from the message before comparing PO references at
    # all, so a cross-customer reference can never come back as a match.
    account_id = (arguments.get("account_id") or "").strip().lower()
    owner_id = (row.get("_customerid_value") or "").strip().lower()
    belongs_to_customer = bool(account_id) and account_id == owner_id

    customer_po = (arguments.get("customer_po_reference") or "").strip().lower()
    linked_po = (row.get("linked_po_reference") or "").strip().lower()
    po_matches_quote = (
        belongs_to_customer and bool(linked_po) and bool(customer_po)
        and linked_po == customer_po
    )
    return {
        "quote": {
            "quote_status": row.get("status") or "",
            "belongs_to_customer": belongs_to_customer,
            "po_matches_quote": po_matches_quote,
            "pump_model": row.get("pump_model") or "",
        }
    }


ORDER_COLUMNS = [
    "salesorderid", "sales_order_reference", "customer_po_reference", "pump_model",
    "order_status", "production_started", "fulfilment_status", "delivery_status",
]


async def _find_order(backend: DynamicsBackend, reference: str) -> dict[str, Any] | None:
    """Resolve an order by our own order number *or* by the customer's PO.

    Customers routinely quote only their own purchase-order number — "spare
    parts for old order: PO 231706" names nothing we index orders by. Matching
    the sales order reference first keeps our number authoritative when both
    could match; falling back to the customer PO means a message that only
    carries the customer's own reference still resolves instead of dropping to
    the not-found route.
    """
    for column in ("sales_order_reference", "customer_po_reference"):
        rows = await backend.query(
            "salesorders",
            select=ORDER_COLUMNS,
            filter_expression=odata.string_filter(column, reference),
            top=1,
        )
        if rows:
            return rows[0]
    return None


async def get_sales_order(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    sales_order_reference = arguments.get("sales_order_reference")
    if not sales_order_reference:
        return {}
    row = await _find_order(backend, sales_order_reference)
    if row is None:
        return {}
    return {
        "order": {
            "order_status": row.get("order_status") or "",
            "production_started": bool(row.get("production_started", False)),
            "sales_order_reference": row.get("sales_order_reference") or "",
            "customer_po_reference": row.get("customer_po_reference") or "",
            "pump_model": row.get("pump_model") or "",
        }
    }


async def get_order_fulfilment_status(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    sales_order_reference = arguments.get("sales_order_reference")
    if not sales_order_reference:
        return {}
    row = await _find_order(backend, sales_order_reference)
    if row is None:
        return {}
    return {
        "fulfilment": {
            "fulfilment_status": row.get("fulfilment_status") or "",
            "delivery_status": row.get("delivery_status") or "",
        }
    }


async def get_inventory_availability(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    pump_model = arguments.get("pump_model")
    if not pump_model:
        return {}
    rows = await backend.query(
        "inventory",
        select=["inventoryid", "pump_model", "availability_status", "lead_time_days"],
        filter_expression=odata.string_filter("pump_model", pump_model),
        top=1,
    )
    if not rows:
        # An unrecognized pump model is not evidence of a supply problem —
        # default to feasible rather than spuriously blocking a routine order.
        # No lead time is quoted, because we genuinely do not have one.
        return {"availability": {"availability_status": "FEASIBLE", "lead_time_days": None}}
    row = rows[0]
    return {
        "availability": {
            "availability_status": row.get("availability_status") or "FEASIBLE",
            "lead_time_days": row.get("lead_time_days"),
        }
    }


async def get_installed_unit(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve an installed pump by serial, scoped to one customer.

    A customer quoting a serial number that turns out to belong to somebody
    else is a routine transcription error, and occasionally something worse.
    Either way the reply must not describe the other customer's equipment, so
    on a mismatch this returns the ownership flag and nothing else — no
    account, no site, no model. The workflow has enough to route the case to a
    human and nothing to leak.
    """
    serial_number = arguments.get("serial_number")
    account_id = (arguments.get("account_id") or "").strip().lower()
    if not serial_number or not account_id:
        return {}
    rows = await backend.query(
        "installedunits",
        select=["installedunitid", "serial_number", "_customerid_value", "pump_model",
                "install_site", "warranty_active", "warranty_end_date"],
        filter_expression=odata.string_filter("serial_number", serial_number),
        top=1,
    )
    if not rows:
        return {}
    row = rows[0]
    if (row.get("_customerid_value") or "").strip().lower() != account_id:
        return {"unit": {"serial_number": serial_number, "belongs_to_customer": False}}
    return {
        "unit": {
            "serial_number": serial_number,
            "belongs_to_customer": True,
            "pump_model": row.get("pump_model") or "",
            "install_site": row.get("install_site") or "",
            "warranty_active": bool(row.get("warranty_active", False)),
            "warranty_end_date": row.get("warranty_end_date") or "",
        }
    }


HANDLERS = {
    "find_customer": find_customer,
    "get_installed_unit": get_installed_unit,
    "get_account_ownership": get_account_ownership,
    "get_credit_status": get_credit_status,
    "get_quote": get_quote,
    "get_sales_order": get_sales_order,
    "get_order_fulfilment_status": get_order_fulfilment_status,
    "get_inventory_availability": get_inventory_availability,
}
