"""Load both fixture files into the real MySQL database.

Idempotent: every insert is `INSERT ... ON DUPLICATE KEY UPDATE`, so running
this twice against the same database re-syncs rows rather than failing on a
duplicate primary key or doubling row counts.

Usage:
    python -m app.mcp.business_records.seed
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mysql.connector

from app.config import settings
from app.mcp.business_records.db import connect, execute_write

_REPO_ROOT = Path(__file__).resolve().parents[3]
D365F_FIXTURES = _REPO_ROOT / "app" / "mcp" / "d365_finance" / "fixtures.json"
CRM_FIXTURES = _REPO_ROOT / "app" / "mcp" / "dynamics" / "fixtures.json"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def _load(path: Path) -> dict[str, list[dict[str, Any]]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dt(value: str | None) -> str | None:
    """ISO 8601 ("...T...Z") -> MySQL DATETIME literal ("... ...").

    The CRM fixtures store timestamps in ISO 8601; MySQL's DATETIME type
    wants a space instead of "T" and no trailing zone marker (everything
    here is already UTC).
    """
    if not value:
        return value
    return value.replace("T", " ").rstrip("Z")


def _upsert(conn, table: str, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{col} = VALUES({col})" for col in columns if col != columns[0])
    query = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    execute_write(conn, query, tuple(row.values()))


def ensure_readonly_user(app_settings: Any = settings) -> None:
    """Create (idempotently) the genuinely lower-privileged credential
    query_readonly runs under (app/mcp/business_records/db.py's
    connect_readonly) — GRANT SELECT only, no INSERT/UPDATE/DELETE/DDL. This
    is the layer of that tool's defense-in-depth that holds even if the verb
    classifier or the READ ONLY transaction mode has a bug.

    Needs its own root connection: the regular app user (business_records_
    mysql_user) deliberately has no CREATE USER privilege. `IF NOT EXISTS`/
    re-running the GRANT makes this safe to call on every seed, the same
    idempotency `apply_schema`/`_upsert` already have.
    """
    conn = mysql.connector.connect(
        host=app_settings.business_records_mysql_host,
        port=app_settings.business_records_mysql_port,
        user="root",
        password=app_settings.business_records_mysql_root_password,
        database=app_settings.business_records_mysql_database,
    )
    cursor = conn.cursor()
    try:
        cursor.execute(
            "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s",
            (
                app_settings.business_records_readonly_mysql_user,
                app_settings.business_records_readonly_mysql_password,
            ),
        )
        # Table/column/user names cannot be parameterized (%s only binds
        # values) — safe here regardless, since both are deployment
        # configuration, never workflow- or request-supplied input.
        cursor.execute(
            f"GRANT SELECT ON {app_settings.business_records_mysql_database}.* "
            f"TO %s@'%%'",
            (app_settings.business_records_readonly_mysql_user,),
        )
        cursor.execute("FLUSH PRIVILEGES")
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def apply_schema(conn) -> None:
    # Strip full-line `--` comments before splitting on `;` — a section-header
    # comment block glued onto the statement that follows it would otherwise
    # make the whole combined chunk start with "--" and get silently treated
    # as pure comment, dropping the real CREATE TABLE with it.
    lines = (
        line for line in SCHEMA_SQL.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    sql = "\n".join(lines)
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            cursor = conn.cursor()
            try:
                cursor.execute(statement)
            finally:
                cursor.close()
    conn.commit()


def seed_d365_finance(conn) -> dict[str, int]:
    data = _load(D365F_FIXTURES)
    counts: dict[str, int] = {}

    for row in data.get("customers", []):
        _upsert(conn, "d365f_customers", {
            "customerid": row["customerid"],
            "customer_account": row.get("customer_account"),
            "name": row["name"],
            "data_area_id": row.get("data_area_id"),
            "sales_region": row.get("sales_region"),
            "key_account": bool(row.get("key_account", False)),
            "account_owner_name": row.get("account_owner_name") or None,
            "territory_sales_owner_name": row.get("territory_sales_owner_name") or None,
            "sales_engineer_name": row.get("sales_engineer_name") or None,
            "application_specialist_name": row.get("application_specialist_name") or None,
            "service_owner_name": row.get("service_owner_name") or None,
            "credit_hold": bool(row.get("credit_hold", False)),
        })
    counts["d365f_customers"] = len(data.get("customers", []))

    for row in data.get("employees", []):
        _upsert(conn, "d365f_employees", {
            "employeeid": row["employeeid"],
            "display_name": row["display_name"],
            "active": bool(row.get("active", True)),
            "team_name": row.get("team_name"),
        })
    counts["d365f_employees"] = len(data.get("employees", []))

    for row in data.get("quotes", []):
        _upsert(conn, "d365f_quotes", {
            "quoteid": row["quoteid"],
            "quotation_number": row["quotation_number"],
            "customerid": row.get("_customerid_value"),
            "status": row.get("status"),
            "purchase_order_number": row.get("purchase_order_number"),
            "pump_model": row.get("pump_model"),
        })
    counts["d365f_quotes"] = len(data.get("quotes", []))

    for row in data.get("salesorders", []):
        _upsert(conn, "d365f_salesorders", {
            "salesorderid": row["salesorderid"],
            "order_number": row["order_number"],
            "purchase_order_number": row.get("purchase_order_number"),
            "customerid": row.get("_customerid_value"),
            "pump_model": row.get("pump_model"),
            "order_status": row.get("order_status"),
            "production_started": bool(row.get("production_started", False)),
            "fulfilment_status": row.get("fulfilment_status"),
            "delivery_status": row.get("delivery_status"),
        })
    counts["d365f_salesorders"] = len(data.get("salesorders", []))

    for row in data.get("shipments", []):
        _upsert(conn, "d365f_shipments", {
            "shipmentid": row["shipmentid"],
            "shipment_number": row["shipment_number"],
            "order_number": row.get("order_number"),
            "purchase_order_number": row.get("purchase_order_number"),
            "customerid": row.get("_customerid_value"),
            "status": row.get("status"),
            "tracking_number": row.get("tracking_number"),
            "delivery_status": row.get("delivery_status"),
        })
    counts["d365f_shipments"] = len(data.get("shipments", []))

    for row in data.get("invoices", []):
        _upsert(conn, "d365f_invoices", {
            "invoiceid": row["invoiceid"],
            "invoice_number": row["invoice_number"],
            "order_number": row.get("order_number"),
            "purchase_order_number": row.get("purchase_order_number"),
            "customerid": row.get("_customerid_value"),
            "status": row.get("status"),
            "total_amount": row.get("total_amount"),
            "currency": row.get("currency"),
        })
    counts["d365f_invoices"] = len(data.get("invoices", []))

    for row in data.get("contracts", []):
        _upsert(conn, "d365f_contracts", {
            "contractid": row["contractid"],
            "contract_number": row["contract_number"],
            "customerid": row.get("_customerid_value"),
            "name": row.get("name"),
            "status": row.get("status"),
            "valid_from": row.get("valid_from"),
            "valid_until": row.get("valid_until"),
        })
    counts["d365f_contracts"] = len(data.get("contracts", []))

    for row in data.get("inventory", []):
        _upsert(conn, "d365f_inventory", {
            "inventoryid": row["inventoryid"],
            "pump_model": row["pump_model"],
            "availability_status": row.get("availability_status"),
            "lead_time_days": row.get("lead_time_days"),
        })
    counts["d365f_inventory"] = len(data.get("inventory", []))

    for row in data.get("installedunits", []):
        _upsert(conn, "d365f_installedunits", {
            "installedunitid": row["installedunitid"],
            "serial_number": row["serial_number"],
            "customerid": row.get("_customerid_value"),
            "pump_model": row.get("pump_model"),
            "existing_pump_manufacturer": row.get("existing_pump_manufacturer"),
            "site_or_location": row.get("site_or_location"),
            "warranty_active": bool(row.get("warranty_active", False)),
            "warranty_end_date": row.get("warranty_end_date"),
            "existing_pump_performance": row.get("existing_pump_performance"),
        })
    counts["d365f_installedunits"] = len(data.get("installedunits", []))

    core_product_fields = {"productid", "product_name", "pump_model", "product_family", "manufacturer"}
    for row in data.get("products", []):
        specs = {k: v for k, v in row.items() if k not in core_product_fields}
        _upsert(conn, "d365f_products", {
            "productid": row["productid"],
            "product_name": row["product_name"],
            "pump_model": row.get("pump_model"),
            "product_family": row.get("product_family"),
            "manufacturer": row.get("manufacturer"),
            "specs": json.dumps(specs, ensure_ascii=False),
        })
    counts["d365f_products"] = len(data.get("products", []))

    return counts


def seed_dynamics_crm(conn) -> dict[str, int]:
    data = _load(CRM_FIXTURES)
    counts: dict[str, int] = {}

    for row in data.get("accounts", []):
        _upsert(conn, "crm_accounts", {
            "accountid": row["accountid"],
            "name": row["name"],
            "accountnumber": row.get("accountnumber"),
            "industrycode": row.get("industrycode"),
            "address1_city": row.get("address1_city"),
            "address1_country": row.get("address1_country"),
            "telephone1": row.get("telephone1"),
            "websiteurl": row.get("websiteurl"),
            "statecode": int(row.get("statecode", 0)),
        })
    counts["crm_accounts"] = len(data.get("accounts", []))

    for row in data.get("contacts", []):
        _upsert(conn, "crm_contacts", {
            "contactid": row["contactid"],
            "fullname": row["fullname"],
            "emailaddress1": row.get("emailaddress1"),
            "telephone1": row.get("telephone1"),
            "jobtitle": row.get("jobtitle"),
            "accountid": row.get("_parentcustomerid_value"),
        })
    counts["crm_contacts"] = len(data.get("contacts", []))

    for row in data.get("opportunities", []):
        _upsert(conn, "crm_opportunities", {
            "opportunityid": row["opportunityid"],
            "name": row["name"],
            "estimatedvalue": row.get("estimatedvalue"),
            "estimatedclosedate": row.get("estimatedclosedate"),
            "statecode": int(row.get("statecode", 0)),
            "accountid": row.get("_customerid_value"),
        })
    counts["crm_opportunities"] = len(data.get("opportunities", []))

    for row in data.get("quotations", []):
        _upsert(conn, "crm_quotations", {
            "quoteid": row["quoteid"],
            "quotation_number": row["quotation_number"],
            "name": row.get("name"),
            "status": row.get("status"),
            "totalamount": row.get("totalamount"),
            "accountid": row.get("_customerid_value"),
            "opportunityid": row.get("_opportunityid_value"),
        })
    counts["crm_quotations"] = len(data.get("quotations", []))

    salesorder_product_rows = 0
    for row in data.get("salesorders", []):
        _upsert(conn, "crm_salesorders", {
            "salesorderid": row["salesorderid"],
            "order_number": row["order_number"],
            "purchase_order_number": row.get("purchase_order_number"),
            "name": row.get("name"),
            "createdon": _dt(row.get("createdon")),
            "confirmed_date": _dt(row.get("confirmed_date")),
            "status": row.get("status"),
            "totalamount": row.get("totalamount"),
            "accountid": row.get("_customerid_value"),
        })
        # Re-seedable without duplicating line items: clear this order's
        # products before re-inserting, since the child rows have no natural
        # unique key of their own to upsert against.
        execute_write(
            conn,
            "DELETE FROM crm_salesorder_products WHERE salesorderid = %s",
            (row["salesorderid"],),
        )
        for product in row.get("products", []):
            execute_write(
                conn,
                "INSERT INTO crm_salesorder_products "
                "(salesorderid, name, product_number, serial_number, quantity) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    row["salesorderid"],
                    product.get("name"),
                    product.get("product_number"),
                    product.get("serial_number"),
                    int(product.get("quantity", 1)),
                ),
            )
            salesorder_product_rows += 1
    counts["crm_salesorders"] = len(data.get("salesorders", []))
    counts["crm_salesorder_products"] = salesorder_product_rows

    for row in data.get("shipments", []):
        _upsert(conn, "crm_shipments", {
            "shipmentid": row["shipmentid"],
            "shipment_number": row["shipment_number"],
            "status": row.get("status"),
            "shipped_date": _dt(row.get("shipped_date")),
            "delivered_date": _dt(row.get("delivered_date")),
            "salesorderid": row.get("_salesorderid_value"),
            "accountid": row.get("_customerid_value"),
        })
    counts["crm_shipments"] = len(data.get("shipments", []))

    for row in data.get("service_cases", []):
        _upsert(conn, "crm_service_cases", {
            "caseid": row["caseid"],
            "service_case_number": row["service_case_number"],
            "title": row["title"],
            "status": row.get("status", "open"),
            "priority": row.get("priority", "normal"),
            "serial_number": row.get("serial_number"),
            "accountid": row.get("_customerid_value"),
        })
    counts["crm_service_cases"] = len(data.get("service_cases", []))

    for row in data.get("contracts", []):
        _upsert(conn, "crm_contracts", {
            "contractid": row["contractid"],
            "contract_number": row["contract_number"],
            "accountid": row.get("_customerid_value"),
            "name": row.get("name"),
            "status": row.get("status"),
        })
    counts["crm_contracts"] = len(data.get("contracts", []))

    for row in data.get("installed_equipment", []):
        _upsert(conn, "crm_installed_equipment", {
            "equipmentid": row["equipmentid"],
            "serial_number": row["serial_number"],
            "accountid": row.get("_customerid_value"),
            "product_name": row.get("product_name"),
            "pump_model": row.get("pump_model"),
            "existing_pump_manufacturer": row.get("existing_pump_manufacturer"),
            "site_or_location": row.get("site_or_location"),
            "existing_pump_performance": row.get("existing_pump_performance"),
        })
    counts["crm_installed_equipment"] = len(data.get("installed_equipment", []))

    for row in data.get("products", []):
        _upsert(conn, "crm_products", {
            "productid": row["productid"],
            "product_name": row["product_name"],
            "productnumber": row.get("productnumber"),
            "pump_model": row.get("pump_model"),
            "product_family": row.get("product_family"),
            "description": row.get("description"),
        })
    counts["crm_products"] = len(data.get("products", []))

    for row in data.get("activitypointers", []):
        _upsert(conn, "crm_activitypointers", {
            "activityid": row["activityid"],
            "activitytypecode": row.get("activitytypecode"),
            "subject": row.get("subject"),
            "createdon": _dt(row.get("createdon")),
            "statecode": int(row.get("statecode", 0)),
            "regardingobjectid": row.get("_regardingobjectid_value"),
        })
    counts["crm_activitypointers"] = len(data.get("activitypointers", []))

    return counts


def seed_all(app_settings=settings) -> dict[str, int]:
    conn = connect(app_settings)
    try:
        apply_schema(conn)
        counts = {}
        counts.update(seed_d365_finance(conn))
        counts.update(seed_dynamics_crm(conn))
        ensure_readonly_user(app_settings)
        return counts
    finally:
        conn.close()


if __name__ == "__main__":
    result = seed_all()
    for table, count in sorted(result.items()):
        print(f"{table}: {count} rows")
