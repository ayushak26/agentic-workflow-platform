"""Real, live MySQL-backed business-records MCP server.

Unlike app/mcp/dynamics/ and app/mcp/d365_finance/ (fixture-backed mocks,
data held in memory and reloaded from JSON on every process start), this
package talks to a genuinely persistent MySQL database — seeded once from
the same two fixture files those mock servers use
(app/mcp/dynamics/fixtures.json, app/mcp/d365_finance/fixtures.json) via
schema.sql + seed.py, then read and written for real.

Exposes a small, explicitly classified tool set (customer_search,
order_search, inventory_check, product_search — read; create_case,
create_opportunity, create_order — write; update_order, update_case — write)
rather than a raw SQL executor: every tool is a real parameterized query
against a specific table with a specific, documented purpose, so this
platform's per-tool operation classification (app/mcp/policy.py) has
something real to attach to.
"""
