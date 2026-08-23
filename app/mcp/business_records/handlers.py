"""Real, parameterized-SQL handlers for the business-records MCP server.

Each function implements exactly one entry in `tools.TOOL_DEFINITIONS` — the
keys of `HANDLERS` below must match `TOOLS_BY_NAME` exactly (mirrors
app/mcp/d365_finance/handlers.py's own contract, which this module follows
closely). Every query is parameterized (`%s` placeholders via
`app.mcp.business_records.db.execute`/`execute_write`) — never string-built.
"""
from __future__ import annotations

import json
from typing import Any

from mysql.connector.connection import MySQLConnectionAbstract

from app.mcp.business_records.db import execute, execute_write


def _collection(rows: list[dict[str, Any]], key: str, limit: int) -> dict[str, Any]:
    """Internal helper for the collection step.

    Args:
        rows (list[dict[str, Any]]): Table rows.
        key (str): Lookup key.
        limit (int): Maximum number of items to return.

    Returns:
        dict[str, Any]: The result.
    """
    truncated = len(rows) > limit
    return {key: rows[:limit], "count": min(len(rows), limit), "truncated": truncated}


def _limit(arguments: dict[str, Any], default: int, maximum: int) -> int:
    """Internal helper for the limit step.

    Args:
        arguments (dict[str, Any]): The arguments.
        default (int): Default value.
        maximum (int): The maximum.

    Returns:
        int: The result.
    """
    value = arguments.get("limit", default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


# ------------------------------------------------------------------- READ --

async def customer_search(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Compute the customer search.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The search.
    """
    name = arguments.get("customer_name")
    limit = _limit(arguments, 5, 25)
    if not name:
        return _collection([], "customers", limit)
    pattern = f"%{name}%"

    d365f_rows = execute(
        conn,
        "SELECT customerid AS id, name, 'd365f' AS source, sales_region AS region, "
        "key_account, credit_hold FROM d365f_customers WHERE name LIKE %s "
        "ORDER BY name LIMIT %s",
        (pattern, limit + 1),
    )
    crm_rows = execute(
        conn,
        "SELECT accountid AS id, name, 'crm' AS source, address1_country AS region, "
        "NULL AS key_account, NULL AS credit_hold FROM crm_accounts WHERE name LIKE %s "
        "ORDER BY name LIMIT %s",
        (pattern, limit + 1),
    )
    combined = [
        {
            "id": row["id"],
            "name": row["name"],
            "source": row["source"],
            "region": row.get("region") or "",
            "key_account": bool(row["key_account"]) if row["key_account"] is not None else None,
            "credit_hold": bool(row["credit_hold"]) if row["credit_hold"] is not None else None,
        }
        for row in [*d365f_rows, *crm_rows]
    ]
    return _collection(combined, "customers", limit)


async def order_search(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Compute the order search.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The search.
    """
    order_number = arguments.get("order_number")
    purchase_order_number = arguments.get("purchase_order_number")
    limit = _limit(arguments, 5, 25)
    if not order_number and not purchase_order_number:
        return _collection([], "orders", limit)

    values = [v for v in (order_number, purchase_order_number) if v]
    d365f_rows: list[dict[str, Any]] = []
    crm_rows: list[dict[str, Any]] = []
    for value in values:
        d365f_rows.extend(execute(
            conn,
            "SELECT salesorderid AS id, order_number, purchase_order_number, "
            "'d365f' AS source, order_status AS status, fulfilment_status, "
            "delivery_status FROM d365f_salesorders "
            "WHERE order_number = %s OR purchase_order_number = %s "
            "ORDER BY order_number LIMIT %s",
            (value, value, limit + 1),
        ))
        crm_rows.extend(execute(
            conn,
            "SELECT salesorderid AS id, order_number, purchase_order_number, "
            "'crm' AS source, status, NULL AS fulfilment_status, "
            "NULL AS delivery_status FROM crm_salesorders "
            "WHERE order_number = %s OR purchase_order_number = %s "
            "ORDER BY order_number LIMIT %s",
            (value, value, limit + 1),
        ))

    seen: set[str] = set()
    combined: list[dict[str, Any]] = []
    for row in [*d365f_rows, *crm_rows]:
        key = f"{row['source']}:{row['id']}"
        if key in seen:
            continue
        seen.add(key)
        combined.append({
            "order_number": row["order_number"] or "",
            "purchase_order_number": row["purchase_order_number"] or "",
            "source": row["source"],
            "status": row.get("status") or row.get("fulfilment_status") or "",
            "delivery_status": row.get("delivery_status") or "",
        })
    return _collection(combined, "orders", limit)


async def inventory_check(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Compute the inventory check.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The check.
    """
    pump_model = arguments.get("pump_model")
    if not pump_model:
        return {"inventory": [{"pump_model": "", "availability_status": "FEASIBLE", "lead_time_days": None}], "count": 1, "truncated": False}
    rows = execute(
        conn,
        "SELECT pump_model, availability_status, lead_time_days FROM d365f_inventory "
        "WHERE pump_model = %s LIMIT 1",
        (pump_model,),
    )
    if not rows:
        # An unrecognized/unstocked pump model is not evidence of a supply
        # problem — default to feasible rather than spuriously blocking a
        # routine order. No lead time is quoted, because none is known.
        return {
            "inventory": [{"pump_model": pump_model, "availability_status": "FEASIBLE", "lead_time_days": None}],
            "count": 1, "truncated": False,
        }
    return {"inventory": rows, "count": len(rows), "truncated": False}


async def product_search(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Compute the product search.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The search.
    """
    product_name = arguments.get("product_name")
    product_family = arguments.get("product_family")
    limit = _limit(arguments, 10, 25)
    if not product_name and not product_family:
        return _collection([], "products", limit)

    clauses = []
    params: list[Any] = []
    if product_name:
        clauses.append("product_name LIKE %s")
        params.append(f"%{product_name}%")
    if product_family:
        clauses.append("product_family LIKE %s")
        params.append(f"%{product_family}%")
    where = " OR ".join(clauses)

    d365f_rows = execute(
        conn,
        f"SELECT product_name, pump_model, product_family, manufacturer, specs, "
        f"'d365f' AS source FROM d365f_products WHERE {where} "
        f"ORDER BY product_name LIMIT %s",
        (*params, limit + 1),
    )
    crm_rows = execute(
        conn,
        f"SELECT product_name, pump_model, product_family, NULL AS manufacturer, "
        f"NULL AS specs, 'crm' AS source FROM crm_products WHERE {where} "
        f"ORDER BY product_name LIMIT %s",
        (*params, limit + 1),
    )
    combined = []
    for row in [*d365f_rows, *crm_rows]:
        specs = row.get("specs")
        if isinstance(specs, str):
            try:
                specs = json.loads(specs)
            except json.JSONDecodeError:
                specs = {}
        combined.append({
            "product_name": row["product_name"] or "",
            "pump_model": row["pump_model"] or "",
            "product_family": row["product_family"] or "",
            "manufacturer": row.get("manufacturer") or "",
            "source": row["source"],
            "specs": specs or {},
        })
    return _collection(combined, "products", limit)


async def query_readonly(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Deliberately ignores `conn` — the shared module-level connection
    (server.py's backend()) authenticates as the full-privilege app user.
    This is the one tool that must run under the genuinely lower-privileged
    read-only credential instead (see sql_guard.py's module docstring for
    the full defense-in-depth this goes through).
    """
    del conn
    from app.mcp.business_records.db import connect_readonly
    from app.mcp.business_records.sql_guard import run_readonly_query

    sql = arguments.get("sql") or ""
    params = arguments.get("params") or {}
    max_rows = _limit(arguments, 100, 500)
    timeout_seconds = min(max(float(arguments.get("timeout_seconds", 10) or 10), 1.0), 30.0)
    return await run_readonly_query(
        sql, params,
        connect=connect_readonly,
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
    )


# ------------------------------------------------------------------ WRITE --

async def create_case(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create the case.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The case.
    """
    account_id = arguments.get("account_id")
    title = arguments.get("title")
    priority = arguments.get("priority", "normal")
    serial_number = arguments.get("serial_number")
    case_id = f"case-{_next_suffix(conn, 'crm_service_cases', 'caseid', 'case-')}"
    case_number = f"CASE-{_next_suffix(conn, 'crm_service_cases', 'service_case_number', 'CASE-'):04d}"
    execute_write(
        conn,
        "INSERT INTO crm_service_cases "
        "(caseid, service_case_number, title, status, priority, serial_number, accountid) "
        "VALUES (%s, %s, %s, 'open', %s, %s, %s)",
        (case_id, case_number, title, priority, serial_number, account_id),
    )
    return {"case": {
        "case_id": case_id, "service_case_number": case_number, "title": title,
        "status": "open", "priority": priority, "account_id": account_id,
    }}


async def create_opportunity(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create the opportunity.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The opportunity.
    """
    account_id = arguments.get("account_id")
    name = arguments.get("name")
    estimated_value = arguments.get("estimated_value")
    estimated_close_date = arguments.get("estimated_close_date")
    opportunity_id = f"opp-{_next_suffix(conn, 'crm_opportunities', 'opportunityid', 'opp-')}"
    execute_write(
        conn,
        "INSERT INTO crm_opportunities "
        "(opportunityid, name, estimatedvalue, estimatedclosedate, statecode, accountid) "
        "VALUES (%s, %s, %s, %s, 0, %s)",
        (opportunity_id, name, estimated_value, estimated_close_date, account_id),
    )
    return {"opportunity": {
        "opportunity_id": opportunity_id, "name": name,
        "estimated_value": estimated_value, "account_id": account_id,
    }}


async def create_order(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create the order.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The order.
    """
    account_id = arguments.get("account_id")
    order_number = arguments.get("order_number")
    name = arguments.get("name")
    purchase_order_number = arguments.get("purchase_order_number")
    total_amount = arguments.get("total_amount")
    order_id = f"so-{_next_suffix(conn, 'crm_salesorders', 'salesorderid', 'so-')}"
    execute_write(
        conn,
        "INSERT INTO crm_salesorders "
        "(salesorderid, order_number, purchase_order_number, name, status, totalamount, accountid) "
        "VALUES (%s, %s, %s, %s, 'draft', %s, %s)",
        (order_id, order_number, purchase_order_number, name, total_amount, account_id),
    )
    return {"order": {
        "order_id": order_id, "order_number": order_number, "status": "draft",
        "account_id": account_id,
    }}


def _order_view(row: dict[str, Any]) -> dict[str, Any]:
    """Normalizes a raw `crm_salesorders` row to create_order's field names —
    a workflow mapping `order_number`/`account_id` after an update must not
    have to switch to `accountid` because this tool happened to read the row
    back from SQL instead of building it in Python."""
    return {
        "order_id": row.get("salesorderid"),
        "order_number": row.get("order_number"),
        "purchase_order_number": row.get("purchase_order_number"),
        "name": row.get("name"),
        "status": row.get("status"),
        "total_amount": row.get("totalamount"),
        "account_id": row.get("accountid"),
    }


async def update_order(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update the order.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The order.
    """
    order_number = arguments.get("order_number")
    if not order_number:
        return {"order": {}, "updated": False}
    rows = execute(conn, "SELECT * FROM crm_salesorders WHERE order_number = %s", (order_number,))
    if not rows:
        return {"order": {}, "updated": False}

    fields: dict[str, Any] = {}
    if arguments.get("status"):
        fields["status"] = arguments["status"]
    if arguments.get("total_amount") is not None:
        fields["totalamount"] = arguments["total_amount"]
    if not fields:
        return {"order": _order_view(rows[0]), "updated": False}

    assignments = ", ".join(f"{col} = %s" for col in fields)
    execute_write(
        conn,
        f"UPDATE crm_salesorders SET {assignments} WHERE order_number = %s",
        (*fields.values(), order_number),
    )
    updated_row = execute(conn, "SELECT * FROM crm_salesorders WHERE order_number = %s", (order_number,))[0]
    return {"order": _order_view(updated_row), "updated": True}


def _case_view(row: dict[str, Any]) -> dict[str, Any]:
    """Normalizes a raw `crm_service_cases` row to create_case's field names
    — see _order_view for why this matters."""
    return {
        "case_id": row.get("caseid"),
        "service_case_number": row.get("service_case_number"),
        "title": row.get("title"),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "serial_number": row.get("serial_number"),
        "account_id": row.get("accountid"),
    }


async def update_case(conn: MySQLConnectionAbstract, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update the case.

    Args:
        conn (MySQLConnectionAbstract): The conn.
        arguments (dict[str, Any]): The arguments.

    Returns:
        dict[str, Any]: The case.
    """
    case_number = arguments.get("service_case_number")
    if not case_number:
        return {"case": {}, "updated": False}
    rows = execute(conn, "SELECT * FROM crm_service_cases WHERE service_case_number = %s", (case_number,))
    if not rows:
        return {"case": {}, "updated": False}

    fields: dict[str, Any] = {}
    if arguments.get("status"):
        fields["status"] = arguments["status"]
    if arguments.get("priority"):
        fields["priority"] = arguments["priority"]
    if not fields:
        return {"case": _case_view(rows[0]), "updated": False}

    assignments = ", ".join(f"{col} = %s" for col in fields)
    execute_write(
        conn,
        f"UPDATE crm_service_cases SET {assignments} WHERE service_case_number = %s",
        (*fields.values(), case_number),
    )
    updated_row = execute(conn, "SELECT * FROM crm_service_cases WHERE service_case_number = %s", (case_number,))[0]
    return {"case": _case_view(updated_row), "updated": True}


def _next_suffix(conn: MySQLConnectionAbstract, table: str, column: str, prefix: str) -> int:
    """A small counter derived from how many rows already start with `prefix`
    — good enough for demo-scale seeded data; a real deployment would use the
    source system's own id generation instead of this."""
    rows = execute(conn, f"SELECT COUNT(*) AS n FROM {table} WHERE {column} LIKE %s", (f"{prefix}%",))
    return int(rows[0]["n"]) + 1


HANDLERS = {
    "customer_search": customer_search,
    "order_search": order_search,
    "inventory_check": inventory_check,
    "product_search": product_search,
    "query_readonly": query_readonly,
    "create_case": create_case,
    "create_opportunity": create_opportunity,
    "create_order": create_order,
    "update_order": update_order,
    "update_case": update_case,
}
