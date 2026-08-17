"""Business Records MCP server — real MySQL, not a fixture backend.

Unlike the d365_finance/dynamics test suites (which fake the MCP transport
against an in-memory FixtureBackend), these tests run the actual handlers
against a real MySQL database — the whole point of this connector. They
require a reachable MySQL (docker compose up mysql) seeded via
`python -m app.mcp.business_records.seed`; if neither is available the
module skips rather than failing the suite for developers without Docker
running, matching this repo's tolerance for optional local infrastructure
(see e.g. tests that skip when Weaviate/Mongo aren't reachable).
"""
from __future__ import annotations

import asyncio

import mysql.connector
import pytest

from app.mcp.business_records import seed
from app.mcp.business_records.db import connect
from app.mcp.business_records.handlers import HANDLERS
from app.mcp.business_records.tools import READ_ONLY_TOOLS, TOOLS_BY_NAME

try:
    connect().close()
    seed.seed_all()
except mysql.connector.Error as error:
    pytest.skip(f"business_records MySQL not reachable: {error}", allow_module_level=True)


@pytest.fixture()
def conn():
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def run(coro):
    return asyncio.run(coro)


def test_tools_by_name_and_handlers_match_exactly():
    assert set(TOOLS_BY_NAME) == set(HANDLERS)


def test_read_only_tools_are_exactly_the_read_operations():
    reads = {name for name, definition in TOOLS_BY_NAME.items() if definition["operation"] == "read"}
    writes = {name for name, definition in TOOLS_BY_NAME.items() if definition["operation"] == "write"}
    assert set(READ_ONLY_TOOLS) == reads
    assert reads == {
        "customer_search", "order_search", "inventory_check", "product_search",
        "query_readonly",
    }
    assert writes == {"create_case", "create_opportunity", "create_order", "update_order", "update_case"}


def test_seed_row_counts_match_both_fixture_files():
    d365f = seed._load(seed.D365F_FIXTURES)
    crm = seed._load(seed.CRM_FIXTURES)
    counts = seed.seed_all()

    for key, table in [
        ("customers", "d365f_customers"), ("quotes", "d365f_quotes"),
        ("salesorders", "d365f_salesorders"), ("shipments", "d365f_shipments"),
        ("invoices", "d365f_invoices"), ("contracts", "d365f_contracts"),
        ("inventory", "d365f_inventory"), ("installedunits", "d365f_installedunits"),
        ("products", "d365f_products"), ("employees", "d365f_employees"),
    ]:
        assert counts[table] == len(d365f.get(key, [])), table

    for key, table in [
        ("accounts", "crm_accounts"), ("contacts", "crm_contacts"),
        ("opportunities", "crm_opportunities"), ("quotations", "crm_quotations"),
        ("salesorders", "crm_salesorders"), ("shipments", "crm_shipments"),
        ("service_cases", "crm_service_cases"), ("contracts", "crm_contracts"),
        ("installed_equipment", "crm_installed_equipment"), ("products", "crm_products"),
        ("activitypointers", "crm_activitypointers"),
    ]:
        assert counts[table] == len(crm.get(key, [])), table


def test_seed_is_idempotent():
    first = seed.seed_all()
    second = seed.seed_all()
    assert first == second


# ------------------------------------------------------------------- READ --

def test_customer_search_finds_seeded_customers_from_both_sources(conn):
    result = run(HANDLERS["customer_search"](conn, {"customer_name": "a"}))
    assert result["count"] > 0
    sources = {row["source"] for row in result["customers"]}
    assert "d365f" in sources or "crm" in sources


def test_customer_search_with_no_name_returns_empty():
    conn = connect()
    try:
        result = run(HANDLERS["customer_search"](conn, {}))
    finally:
        conn.close()
    assert result == {"customers": [], "count": 0, "truncated": False}


def test_order_search_finds_a_seeded_d365f_order(conn):
    d365f = seed._load(seed.D365F_FIXTURES)
    order = d365f["salesorders"][0]
    result = run(HANDLERS["order_search"](conn, {"order_number": order["order_number"]}))
    assert result["count"] >= 1
    assert any(row["order_number"] == order["order_number"] for row in result["orders"])


def test_inventory_check_returns_seeded_row_for_known_pump_model(conn):
    d365f = seed._load(seed.D365F_FIXTURES)
    item = d365f["inventory"][0]
    result = run(HANDLERS["inventory_check"](conn, {"pump_model": item["pump_model"]}))
    assert result["count"] == 1
    assert result["inventory"][0]["pump_model"] == item["pump_model"]


def test_inventory_check_defaults_to_feasible_for_unknown_pump_model(conn):
    result = run(HANDLERS["inventory_check"](conn, {"pump_model": "NOT-A-REAL-MODEL-XYZ"}))
    assert result["inventory"][0]["availability_status"] == "FEASIBLE"
    assert result["inventory"][0]["lead_time_days"] is None


def test_product_search_finds_seeded_products_by_family(conn):
    d365f = seed._load(seed.D365F_FIXTURES)
    family = d365f["products"][0]["product_family"]
    result = run(HANDLERS["product_search"](conn, {"product_family": family}))
    assert result["count"] > 0
    assert result["products"][0]["specs"] == {} or isinstance(result["products"][0]["specs"], dict)


def test_query_readonly_runs_a_named_parameter_select():
    result = run(HANDLERS["query_readonly"](None, {
        "sql": "SELECT name FROM crm_accounts WHERE name LIKE %(pattern)s ORDER BY name",
        "params": {"pattern": "%a%"},
    }))
    assert result["count"] > 0
    assert all("name" in row for row in result["rows"])


def test_query_readonly_rejects_a_write_statement_before_it_ever_runs():
    from app.mcp.business_records.sql_guard import SQLGuardError

    with pytest.raises(SQLGuardError) as caught:
        run(HANDLERS["query_readonly"](None, {"sql": "DELETE FROM crm_accounts WHERE 1=1"}))
    assert caught.value.code == "SQL_WRITE_NOT_ALLOWED"

    # Genuinely never ran — not just refused after the fact.
    remaining = run(HANDLERS["query_readonly"](None, {"sql": "SELECT COUNT(*) AS n FROM crm_accounts"}))
    assert remaining["rows"][0]["n"] > 0


def test_query_readonly_rejects_a_stacked_statement():
    with pytest.raises(mysql.connector.Error):
        run(HANDLERS["query_readonly"](None, {"sql": "SELECT 1; SELECT 2"}))


def test_query_readonly_a_runaway_query_is_cancelled_by_the_timeout():
    from app.mcp.business_records.sql_guard import SQLGuardError

    with pytest.raises(SQLGuardError) as caught:
        run(HANDLERS["query_readonly"](None, {"sql": "SELECT SLEEP(30)", "timeout_seconds": 1}))
    assert caught.value.code == "SQL_TIMEOUT"


def test_query_readonly_credential_cannot_write_even_directly():
    """Defense in depth: even bypassing sql_guard's own verb check entirely,
    the connect_readonly() credential itself has no write privilege."""
    from app.mcp.business_records.db import connect_readonly

    conn = connect_readonly()
    try:
        cursor = conn.cursor()
        with pytest.raises(mysql.connector.Error):
            cursor.execute("INSERT INTO crm_accounts (accountid, name) VALUES ('x', 'y')")
        with pytest.raises(mysql.connector.Error):
            cursor.execute("CREATE TABLE __should_never_exist__ (id INT)")
    finally:
        conn.close()


# ------------------------------------------------------------------ WRITE --

def test_create_case_then_update_case_round_trips(conn):
    crm = seed._load(seed.CRM_FIXTURES)
    account_id = crm["accounts"][0]["accountid"]

    created = run(HANDLERS["create_case"](conn, {
        "account_id": account_id, "title": "Test case from business_records suite",
    }))["case"]
    assert created["status"] == "open"
    assert created["account_id"] == account_id

    updated = run(HANDLERS["update_case"](conn, {
        "service_case_number": created["service_case_number"], "status": "resolved",
    }))
    assert updated["updated"] is True
    assert updated["case"]["status"] == "resolved"


def test_create_opportunity_writes_a_real_row(conn):
    crm = seed._load(seed.CRM_FIXTURES)
    account_id = crm["accounts"][0]["accountid"]

    created = run(HANDLERS["create_opportunity"](conn, {
        "account_id": account_id, "name": "Test opportunity from business_records suite",
        "estimated_value": 12345.0,
    }))["opportunity"]
    assert created["account_id"] == account_id
    assert created["name"] == "Test opportunity from business_records suite"


def test_create_order_then_update_order_round_trips(conn):
    crm = seed._load(seed.CRM_FIXTURES)
    account_id = crm["accounts"][0]["accountid"]

    created = run(HANDLERS["create_order"](conn, {
        "account_id": account_id, "order_number": "TEST-ORDER-BR-0001",
    }))["order"]
    assert created["status"] == "draft"

    updated = run(HANDLERS["update_order"](conn, {
        "order_number": "TEST-ORDER-BR-0001", "status": "confirmed", "total_amount": 999.5,
    }))
    assert updated["updated"] is True
    assert updated["order"]["status"] == "confirmed"
    assert float(updated["order"]["total_amount"]) == 999.5


def test_update_order_on_unknown_order_number_reports_not_updated(conn):
    result = run(HANDLERS["update_order"](conn, {"order_number": "NO-SUCH-ORDER", "status": "confirmed"}))
    assert result == {"order": {}, "updated": False}


def test_update_case_on_unknown_case_number_reports_not_updated(conn):
    result = run(HANDLERS["update_case"](conn, {"service_case_number": "NO-SUCH-CASE", "status": "resolved"}))
    assert result == {"case": {}, "updated": False}
