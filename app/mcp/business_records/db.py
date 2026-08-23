"""Connection helper for the business-records MySQL database.

One place that knows how to open a connection, so `seed.py` and every tool
handler in `handlers.py` share it rather than each hand-rolling
`mysql.connector.connect(...)`. Every query anywhere in this package goes
through `execute`/`execute_write` below — parameterized (`%s` placeholders,
values passed separately), never string-built — the same discipline
`app/mcp/dynamics/odata.py` already applies to the Dataverse connector.
"""
from __future__ import annotations

from typing import Any

import mysql.connector
from mysql.connector.connection import MySQLConnectionAbstract
from mysql.connector.constants import ClientFlag

from app.config import Settings, settings


def connect(app_settings: Settings = settings) -> MySQLConnectionAbstract:
    """Compute the connect.

    Args:
        app_settings (Settings): The app settings (optional, default settings).

    Returns:
        MySQLConnectionAbstract: The result.
    """
    return mysql.connector.connect(
        host=app_settings.business_records_mysql_host,
        port=app_settings.business_records_mysql_port,
        user=app_settings.business_records_mysql_user,
        password=app_settings.business_records_mysql_password,
        database=app_settings.business_records_mysql_database,
        autocommit=False,
        # Every call site here is already %s-parameterized, so a stacked
        # statement is latent today, not exploitable — but the default
        # negotiation leaves it possible for any query text that ever stops
        # being parameterized. Verified: with this negated, a literal
        # semicolon in query text is rejected as a syntax error instead of
        # running a second statement.
        client_flags=[-ClientFlag.MULTI_STATEMENTS],
    )


def connect_readonly(app_settings: Settings = settings) -> MySQLConnectionAbstract:
    """A genuinely lower-privileged connection for query_readonly
    (app/nodes/sql_query.py) — GRANT SELECT only (see seed.py's
    ensure_readonly_user). This is the one layer of that tool's
    defense-in-depth that holds on its own even if every other layer (verb
    classifier, READ ONLY transaction) has a bug: the account itself simply
    cannot write, full stop.
    """
    return mysql.connector.connect(
        host=app_settings.business_records_mysql_host,
        port=app_settings.business_records_mysql_port,
        user=app_settings.business_records_readonly_mysql_user,
        password=app_settings.business_records_readonly_mysql_password,
        database=app_settings.business_records_mysql_database,
        autocommit=False,
        client_flags=[-ClientFlag.MULTI_STATEMENTS],
    )


def execute(
    conn: MySQLConnectionAbstract, query: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    """Run a parameterized SELECT and return every row as a dict."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(query, params)
        return list(cursor.fetchall())
    finally:
        cursor.close()


def execute_write(
    conn: MySQLConnectionAbstract, query: str, params: tuple[Any, ...] = ()
) -> int:
    """Run a parameterized INSERT/UPDATE, commit, and return the affected/
    inserted row count (or `lastrowid` for an auto-increment insert)."""
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
