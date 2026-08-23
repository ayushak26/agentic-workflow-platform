"""Defense-in-depth for the query_readonly MCP tool.

A SQL snippet is a fundamentally different hazard from every other tool this
server exposes: the workflow author supplies the query text itself, not just
arguments into a fixed, already-reviewed query. Every layer below was
empirically verified individually (see this session's own investigation, and
each function's docstring) rather than assumed from documentation:

1. A genuinely read-only MySQL credential (db.connect_readonly / seed.py's
   ensure_readonly_user) — the *only* layer that holds on its own. Verified:
   it blocks both a DML statement (INSERT) and DDL (CREATE TABLE) at the
   account level, full stop.
2. `client_flags=[-ClientFlag.MULTI_STATEMENTS]` (db.py) — verified: a
   stacked `SELECT 1; SELECT 2` is rejected as a syntax error, not executed
   as two statements.
3. `classify_sql_verb` below — a fast, explainable *authoring-time* refusal
   for an obviously-write statement. Explicitly NOT the enforcement
   boundary: it cannot catch `SELECT SLEEP(30)` or a subquery hiding a write
   verb — layers 1 and 4 are what actually hold.
4. `START TRANSACTION READ ONLY` + always rollback (run_readonly_query) —
   blocks DML. Verified: it does **not** block DDL (a CREATE TABLE inside a
   read-only transaction still succeeds) — which is exactly why layer 1 is
   load-bearing, not optional.
5. A real timeout: `asyncio.wait_for` around the blocking driver call in a
   thread, then `KILL QUERY <connection_id>` from a second connection.
   Verified: `max_execution_time` alone does not stop a running
   `SELECT SLEEP()`; the explicit KILL QUERY does, and the original
   connection stays usable afterward.
6. Named parameters only — this module never receives raw string-built SQL;
   `%(name)s`-style parameters are passed separately to the driver, exactly
   like every other query in this package.
"""
from __future__ import annotations

import asyncio
from typing import Any

from mysql.connector.connection import MySQLConnectionAbstract

from app.mcp.policy import _DESTRUCTIVE_VERBS, _WRITE_VERBS

#: SQL-grammar keywords with no equivalent in an English tool name, so they
#: are not already covered by policy.py's own verb sets (which classify
#: tool *names* like "create_case", not SQL statements) — added here rather
#: than into those shared sets, which would then also affect MCP tool-name
#: classification for an unrelated reason.
_SQL_ONLY_WRITE_VERBS = frozenset({"alter", "grant", "revoke", "replace", "call", "lock"})


class SQLGuardError(RuntimeError):
    """Exception raised for the SQLGuardError case."""
    def __init__(self, message: str, *, code: str):
        """Initialize the SQLGuardError.

        Args:
            message (str): Message text.
            code (str): The code.
        """
        self.code = code
        super().__init__(message)


def classify_sql_verb(sql: str) -> str:
    """"read" or "write", from the statement's first keyword alone.

    Deliberately reuses policy.py's own write/destructive verb sets rather
    than a second list that can drift — "insert"/"update"/"delete"/"drop"/
    "truncate"/"create" already appear there for tool-name classification,
    and mean the same thing here. A handful of SQL-only keywords
    (ALTER/GRANT/...) have no tool-name equivalent, so they're supplemented
    separately.
    """
    first_word = sql.strip().split(None, 1)[:1]
    word = first_word[0].lower() if first_word else ""
    if word in _WRITE_VERBS or word in _DESTRUCTIVE_VERBS or word in _SQL_ONLY_WRITE_VERBS:
        return "write"
    return "read"


async def run_readonly_query(
    sql: str,
    params: dict[str, Any],
    *,
    connect: Any,
    max_rows: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run one read-only query with every layer above applied.

    `connect` is a zero-arg callable returning a fresh connection (normally
    app.mcp.business_records.db.connect_readonly) — a fresh connection per
    call, not a shared/pooled one, so a killed query's connection is simply
    discarded rather than needing to be un-wedged for the next caller.
    """
    verb = classify_sql_verb(sql)
    if verb != "read":
        raise SQLGuardError(
            f"Only read (SELECT) statements are allowed here — this looks "
            f"like a {verb} statement.",
            code="SQL_WRITE_NOT_ALLOWED",
        )

    conn: MySQLConnectionAbstract = connect()
    try:
        cursor = conn.cursor(dictionary=True)
        # READ ONLY blocks DML inside this transaction — verified it does
        # NOT block DDL, which is exactly why the connection itself
        # (connect_readonly) must already be a SELECT-only account.
        cursor.execute("START TRANSACTION READ ONLY")

        def _run() -> list[dict[str, Any]]:
            """Run the result.

            Returns:
                list[dict[str, Any]]: The result.
            """
            cursor.execute(sql, params or None)
            return list(cursor.fetchmany(max_rows + 1))

        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=timeout_seconds,
            )
        except TimeoutError:
            await _kill_query(connect, conn.connection_id)
            raise SQLGuardError(
                f"Query did not finish within {timeout_seconds}s and was "
                "cancelled.",
                code="SQL_TIMEOUT",
            ) from None

        truncated = len(rows) > max_rows
        return {
            "rows": rows[:max_rows],
            "count": min(len(rows), max_rows),
            "truncated": truncated,
        }
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()


async def _kill_query(connect: Any, connection_id: int) -> None:
    """A second, short-lived connection to cancel the first's running
    query — verified this actually stops a running SELECT SLEEP() and
    leaves the original connection usable (which is then closed anyway by
    run_readonly_query's own finally, since its result is now meaningless)."""
    try:
        killer = await asyncio.to_thread(connect)
    except Exception:
        return
    try:
        await asyncio.to_thread(
            lambda: killer.cursor().execute(f"KILL QUERY {int(connection_id)}")
        )
    except Exception:
        pass
    finally:
        killer.close()
