"""Fixture-backed handlers for the d365-finance-scm-mcp business-tool layer.

Each function implements exactly one entry in `app.mcp.d365_finance.tools.
TOOL_DEFINITIONS` — the keys of `HANDLERS` below must match `TOOLS_BY_NAME`
exactly, or a tool becomes dispatchable-but-unimplemented (`server.py`'s
`call_tool` checks both dicts before running anything). Reuses
app.mcp.dynamics's FixtureBackend/odata helpers — the query semantics
(contains/eq filters, $select projection) are OData conventions, not
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


def _any_of_present(*filters: str | None) -> str | None:
    present = [f for f in filters if f]
    return odata.any_of(*present) if present else None


def _priority_filter(*fields: tuple[str, str | None]) -> str | None:
    """Search by the first-given reference only.

    A lookup keyed by several possible identifiers (a quotation number, a
    purchase-order number) prefers the more specific one and falls back to
    the next only when it is absent — never OR them together. A customer's
    PO number is real but belongs to a *different* record than a bogus
    quotation number they also quoted; matching on either would let the PO
    silently resolve a reference that was actually wrong.
    """
    for name, value in fields:
        if value:
            return odata.string_filter(name, value)
    return None


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
    name = arguments.get("customer_name") or arguments.get("company_name")
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


async def find_account_ownership(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
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
        return {"ownership": {}}
    row = await backend.get("customers", account_id, select=CUSTOMER_COLUMNS)
    if row is None:
        return {"ownership": {}}

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


async def find_credit_status(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = arguments.get("account_id")
    if not account_id:
        return {"credit": {}}
    row = await backend.get("customers", account_id, select=["customerid", "credit_hold"])
    if row is None:
        return {"credit": {}}
    return {"credit": {"credit_hold": bool(row.get("credit_hold", False))}}


QUOTE_COLUMNS = [
    "quoteid", "quotation_number", "_customerid_value", "status",
    "purchase_order_number", "pump_model",
]


def _quote(row: dict[str, Any], *, account_id: str, purchase_order_number: str) -> dict[str, Any]:
    owner_id = (row.get("_customerid_value") or "").strip().lower()
    # A quotation number is not proof of ownership. Only assert
    # belongs_to_customer when a confirmed account id was actually supplied —
    # comparing against nothing must never look like a match.
    belongs_to_customer = bool(account_id) and account_id == owner_id
    linked_po = (row.get("purchase_order_number") or "").strip().lower()
    # A quotation reference is not proof of ownership either. Quoting someone
    # else's quote number must never validate: confirm the quote belongs to
    # the account we resolved before comparing PO references at all, so a
    # cross-customer reference can never come back as a match.
    po_matches_quote = (
        belongs_to_customer and bool(linked_po) and bool(purchase_order_number)
        and linked_po == purchase_order_number
    )
    return {
        "quotation_number": row.get("quotation_number") or "",
        "purchase_order_number": row.get("purchase_order_number") or "",
        "status": row.get("status") or "",
        "pump_model": row.get("pump_model") or "",
        "belongs_to_customer": belongs_to_customer,
        "po_matches_quote": po_matches_quote,
    }


async def find_quote(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    """Find a quotation by quotation number or purchase-order number.

    When a confirmed account id is supplied, each match is flagged with
    whether it actually belongs to that customer — quoting someone else's
    quote number must never silently read back as a match for this account.
    """
    quotation_number = arguments.get("quotation_number")
    purchase_order_number = arguments.get("purchase_order_number")
    account_id = (arguments.get("account_id") or "").strip().lower()
    limit = _limit(arguments, 5, 25)
    expression = _priority_filter(
        ("quotation_number", quotation_number),
        ("purchase_order_number", purchase_order_number),
    )
    if expression is None:
        return _collection([], "quotes", limit)
    rows = await backend.query(
        "quotes", select=QUOTE_COLUMNS, filter_expression=expression,
        order_by="quotation_number asc", top=limit + 1,
    )
    po_reference = (purchase_order_number or "").strip().lower()
    return _collection(
        [_quote(row, account_id=account_id, purchase_order_number=po_reference) for row in rows],
        "quotes", limit,
    )


ORDER_COLUMNS = [
    "salesorderid", "order_number", "purchase_order_number", "_customerid_value",
    "pump_model", "order_status", "production_started", "fulfilment_status",
    "delivery_status",
]


def _order_filter(arguments: dict[str, Any]) -> str | None:
    """Customers quote their own purchase-order number at least as often as
    our sales-order number, and a workflow may only have one ambiguous
    reference field to give us — search whichever value(s) were supplied
    against *both* columns, since which kind of reference it is often is not
    knowable in advance. Unlike quotation vs. PO (see find_quote), an order
    number and a PO number genuinely can be the same physical reference
    depending on what the customer wrote, so trying one value against both
    columns is intentional here, not the cross-contamination risk that
    combining a quotation number with an unrelated PO number would be."""
    values = [
        v for v in (arguments.get("order_number"), arguments.get("purchase_order_number")) if v
    ]
    if not values:
        return None
    filters = []
    for value in values:
        filters.append(odata.string_filter("order_number", value))
        filters.append(odata.string_filter("purchase_order_number", value))
    return odata.any_of(*filters)


def _sales_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_number": row.get("order_number") or "",
        "purchase_order_number": row.get("purchase_order_number") or "",
        "pump_model": row.get("pump_model") or "",
        "order_status": row.get("order_status") or "",
        "production_started": bool(row.get("production_started", False)),
        "fulfilment_status": row.get("fulfilment_status") or "",
        "delivery_status": row.get("delivery_status") or "",
    }


async def find_sales_order(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(arguments, 5, 25)
    expression = _order_filter(arguments)
    if expression is None:
        return _collection([], "salesorders", limit)
    rows = await backend.query(
        "salesorders", select=ORDER_COLUMNS, filter_expression=expression,
        order_by="order_number asc", top=limit + 1,
    )
    return _collection([_sales_order(row) for row in rows], "salesorders", limit)


async def find_order_fulfilment_status(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    """Ask why an order is where it is — the customer's wording is identical
    for a material shortage and a quality hold; this is what tells them apart."""
    limit = _limit(arguments, 1, 25)
    expression = _order_filter(arguments)
    if expression is None:
        return {"fulfilment": {}}
    rows = await backend.query(
        "salesorders", select=ORDER_COLUMNS, filter_expression=expression, top=limit,
    )
    if not rows:
        return {"fulfilment": {}}
    row = rows[0]
    return {
        "fulfilment": {
            "fulfilment_status": row.get("fulfilment_status") or "",
            "delivery_status": row.get("delivery_status") or "",
        }
    }


INVENTORY_COLUMNS = ["inventoryid", "pump_model", "availability_status", "lead_time_days"]
PRODUCT_LOOKUP_COLUMNS = ["productid", "product_name", "pump_model", "product_family"]


def _availability(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pump_model": row.get("pump_model") or "",
        "availability_status": row.get("availability_status") or "FEASIBLE",
        "lead_time_days": row.get("lead_time_days"),
    }


async def _resolve_pump_models(backend: DynamicsBackend, arguments: dict[str, Any]) -> list[str]:
    """product_name/product_family describe a catalogue product, not an
    inventory record directly — resolve to the pump model(s) inventory is
    actually keyed by before querying availability."""
    pump_model = arguments.get("pump_model")
    if pump_model:
        return [pump_model]
    product_name = arguments.get("product_name")
    product_family = arguments.get("product_family")
    expression = _any_of_present(
        odata.contains_filter("product_name", product_name) if product_name else None,
        odata.contains_filter("product_family", product_family) if product_family else None,
    )
    if expression is None:
        return []
    rows = await backend.query(
        "products", select=PRODUCT_LOOKUP_COLUMNS, filter_expression=expression, top=25,
    )
    return [row["pump_model"] for row in rows if row.get("pump_model")]


async def find_inventory_availability(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(arguments, 5, 25)
    pump_models = await _resolve_pump_models(backend, arguments)
    if not pump_models:
        return _collection([], "inventory", limit)
    expression = odata.any_of(*(odata.string_filter("pump_model", model) for model in pump_models))
    rows = await backend.query(
        "inventory", select=INVENTORY_COLUMNS, filter_expression=expression,
        order_by="pump_model asc", top=limit + 1,
    )
    if not rows:
        # An unrecognized/unstocked pump model is not evidence of a supply
        # problem — default to feasible rather than spuriously blocking a
        # routine order. No lead time is quoted, because none is known.
        return _collection(
            [
                {"pump_model": model, "availability_status": "FEASIBLE", "lead_time_days": None}
                for model in pump_models
            ],
            "inventory", limit,
        )
    return _collection([_availability(row) for row in rows], "inventory", limit)


INSTALLED_UNIT_COLUMNS = [
    "installedunitid", "serial_number", "_customerid_value", "pump_model",
    "existing_pump_manufacturer", "site_or_location", "warranty_active",
    "warranty_end_date",
]


async def find_installed_unit(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve installed equipment by serial, model, manufacturer or site.

    A customer quoting a serial number that turns out to belong to somebody
    else is a routine transcription error, and occasionally something worse.
    When a confirmed account id is supplied, a mismatch returns only the
    ownership flag and nothing else — no site, no model — so a mistyped digit
    can never describe another customer's equipment.
    """
    serial_number = arguments.get("serial_number")
    existing_pump_model = arguments.get("existing_pump_model")
    existing_pump_manufacturer = arguments.get("existing_pump_manufacturer")
    site_or_location = arguments.get("site_or_location")
    account_id = (arguments.get("account_id") or "").strip().lower()
    limit = _limit(arguments, 5, 25)

    expression = _any_of_present(
        odata.string_filter("serial_number", serial_number) if serial_number else None,
        odata.string_filter("pump_model", existing_pump_model) if existing_pump_model else None,
        odata.string_filter("existing_pump_manufacturer", existing_pump_manufacturer) if existing_pump_manufacturer else None,
        odata.contains_filter("site_or_location", site_or_location) if site_or_location else None,
    )
    if expression is None:
        return _collection([], "installedunits", limit)
    rows = await backend.query(
        "installedunits", select=INSTALLED_UNIT_COLUMNS, filter_expression=expression,
        order_by="serial_number asc", top=limit + 1,
    )

    units: list[dict[str, Any]] = []
    for row in rows:
        owner_id = (row.get("_customerid_value") or "").strip().lower()
        if account_id and owner_id != account_id:
            units.append({"serial_number": row.get("serial_number") or "", "belongs_to_customer": False})
            continue
        units.append({
            "serial_number": row.get("serial_number") or "",
            "belongs_to_customer": bool(account_id),
            "pump_model": row.get("pump_model") or "",
            "existing_pump_manufacturer": row.get("existing_pump_manufacturer") or "",
            "site_or_location": row.get("site_or_location") or "",
            "warranty_active": bool(row.get("warranty_active", False)),
            "warranty_end_date": row.get("warranty_end_date") or "",
        })
    return _collection(units, "installedunits", limit)


SHIPMENT_COLUMNS = [
    "shipmentid", "shipment_number", "order_number", "purchase_order_number",
    "status", "tracking_number", "delivery_status",
]


def _shipment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "shipment_number": row.get("shipment_number") or "",
        "order_number": row.get("order_number") or "",
        "purchase_order_number": row.get("purchase_order_number") or "",
        "status": row.get("status") or "",
        "tracking_number": row.get("tracking_number") or "",
        "delivery_status": row.get("delivery_status") or "",
    }


async def find_shipment(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(arguments, 5, 25)
    expression = _priority_filter(
        ("shipment_number", arguments.get("shipment_number")),
        ("order_number", arguments.get("order_number")),
        ("purchase_order_number", arguments.get("purchase_order_number")),
    )
    if expression is None:
        return _collection([], "shipments", limit)
    rows = await backend.query(
        "shipments", select=SHIPMENT_COLUMNS, filter_expression=expression,
        order_by="shipment_number asc", top=limit + 1,
    )
    return _collection([_shipment(row) for row in rows], "shipments", limit)


INVOICE_COLUMNS = [
    "invoiceid", "invoice_number", "order_number", "purchase_order_number",
    "status", "total_amount", "currency",
]


def _invoice(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_number": row.get("invoice_number") or "",
        "order_number": row.get("order_number") or "",
        "purchase_order_number": row.get("purchase_order_number") or "",
        "status": row.get("status") or "",
        "total_amount": row.get("total_amount"),
        "currency": row.get("currency") or "",
    }


async def find_invoice(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(arguments, 5, 25)
    expression = _priority_filter(
        ("invoice_number", arguments.get("invoice_number")),
        ("order_number", arguments.get("order_number")),
        ("purchase_order_number", arguments.get("purchase_order_number")),
    )
    if expression is None:
        return _collection([], "invoices", limit)
    rows = await backend.query(
        "invoices", select=INVOICE_COLUMNS, filter_expression=expression,
        order_by="invoice_number asc", top=limit + 1,
    )
    return _collection([_invoice(row) for row in rows], "invoices", limit)


CONTRACT_COLUMNS = [
    "contractid", "contract_number", "name", "status", "valid_from", "valid_until",
]


def _contract(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_number": row.get("contract_number") or "",
        "name": row.get("name") or "",
        "status": row.get("status") or "",
        "valid_from": row.get("valid_from") or "",
        "valid_until": row.get("valid_until") or "",
    }


async def find_contract(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(arguments, 5, 25)
    contract_number = arguments.get("contract_number")
    if not contract_number:
        return _collection([], "contracts", limit)
    rows = await backend.query(
        "contracts", select=CONTRACT_COLUMNS,
        filter_expression=odata.string_filter("contract_number", contract_number),
        order_by="contract_number asc", top=limit + 1,
    )
    return _collection([_contract(row) for row in rows], "contracts", limit)


PRODUCT_COLUMNS = [
    "productid", "product_name", "pump_model", "product_family", "manufacturer",
    "flow_rate", "pressure", "temperature", "viscosity", "compatible_fluids",
]


def _product(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_name": row.get("product_name") or "",
        "pump_model": row.get("pump_model") or "",
        "product_family": row.get("product_family") or "",
        "manufacturer": row.get("manufacturer") or "",
        "flow_rate": row.get("flow_rate") or "",
        "pressure": row.get("pressure") or "",
        "temperature": row.get("temperature") or "",
        "viscosity": row.get("viscosity") or "",
        "compatible_fluids": row.get("compatible_fluids") or [],
    }


async def find_products(backend: DynamicsBackend, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(arguments, 5, 25)
    product_name = arguments.get("product_name")
    pump_model = arguments.get("pump_model")
    product_family = arguments.get("product_family")
    expression = _any_of_present(
        odata.contains_filter("product_name", product_name) if product_name else None,
        odata.string_filter("pump_model", pump_model) if pump_model else None,
        odata.contains_filter("product_family", product_family) if product_family else None,
    )
    rows = await backend.query(
        "products", select=PRODUCT_COLUMNS, filter_expression=expression,
        order_by="product_name asc", top=limit + 1,
    )
    return _collection([_product(row) for row in rows], "products", limit)


HANDLERS = {
    "find_customer": find_customer,
    "find_account_ownership": find_account_ownership,
    "find_credit_status": find_credit_status,
    "find_quote": find_quote,
    "find_sales_order": find_sales_order,
    "find_order_fulfilment_status": find_order_fulfilment_status,
    "find_inventory_availability": find_inventory_availability,
    "find_installed_unit": find_installed_unit,
    "find_shipment": find_shipment,
    "find_invoice": find_invoice,
    "find_contract": find_contract,
    "find_products": find_products,
}
