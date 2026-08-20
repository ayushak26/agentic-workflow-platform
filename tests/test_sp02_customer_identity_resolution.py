"""End-to-end coverage for workflows/sp02_customer_identity_resolution.yaml.

Two template references (`compare_names`'s `resolved` input and
`end_resolution`'s `order_status` output, both `outputs.<mcp_node>.first.*`)
had no `?` optional marker. Since `MISSING` — the customer name matching
nothing in Finance & SCM — is this subprocess's most basic documented
outcome, and an omitted `order_reference` (declared optional in this file's
own `inputs:`) legitimately skips the order lookup, both crashed with
`KeyError: Template path not resolvable` instead of producing the documented
`resolution_status`. Fixed by adding the `?` marker; this file locks in that
both "nothing found" paths now complete instead of crashing.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow

WORKFLOW = load_workflow(Path("workflows/sp02_customer_identity_resolution.yaml"))


class FakePythonRunner:
    async def run(self, code, input_fields, *, timeout_seconds, memory_mb):
        namespace = {"inputs": input_fields}
        exec(code, namespace)
        return {"status": "ok", "output": namespace.get("output", {}), "stdout": "", "stderr": "", "duration_s": 0.0}


class FakeMCPService:
    def __init__(self, *, customer_found: bool, order_found: bool) -> None:
        self._customer_found = customer_found
        self._order_found = order_found

    async def call(self, *, server_id, tool_name, arguments, **kwargs):
        if tool_name == "find_customer":
            if not self._customer_found:
                data = {"customers": [], "count": 0}
            else:
                data = {"customers": [{"account_id": "ACC-1", "account_name": "Acme Pumps"}], "count": 1}
            return _read_result(server_id, tool_name, data)
        if tool_name == "find_sales_order":
            if not self._order_found:
                data = {"orders": [], "count": 0}
            else:
                data = {"orders": [{"order_status": "open"}], "count": 1}
            return _read_result(server_id, tool_name, data)
        raise AssertionError(f"unexpected tool: {tool_name}")


def _read_result(server_id, tool_name, data):
    return {
        "server": server_id, "tool": tool_name, "operation": "read",
        "data": data, "text": "", "is_structured": True, "mode": "mock",
        "duration_s": 0.0, "deduplicated": False,
    }


async def _run(*, customer_found: bool, order_found: bool, order_reference: str | None):
    run_id = f"sp02-{uuid.uuid4()}"
    services = {
        "mcp": FakeMCPService(customer_found=customer_found, order_found=order_found),
        "python_runner": FakePythonRunner(),
        "cost_ledger": SimpleNamespace(record=lambda *a, **k: None),
    }
    inputs = {"stated_company_name": "Acme Pumps"}
    if order_reference is not None:
        inputs["order_reference"] = order_reference
    return await run_workflow(WORKFLOW, inputs, services=services, run_id=run_id)


@pytest.mark.asyncio
async def test_customer_not_found_is_a_missing_result_not_a_crash():
    result = await _run(customer_found=False, order_found=False, order_reference=None)
    assert result["status"] == "completed"
    output = result["state"]["node_outputs"]["end_resolution"]["result"]
    assert output["resolution_status"] == "MISSING"
    assert output["resolved_company_name"] == ""


@pytest.mark.asyncio
async def test_omitted_order_reference_skips_the_lookup_without_crashing():
    result = await _run(customer_found=True, order_found=False, order_reference=None)
    assert result["status"] == "completed"
    output = result["state"]["node_outputs"]["end_resolution"]["result"]
    assert output["resolution_status"] == "RESOLVED"
    assert output["order_found"] is False
    assert output["order_status"] is None


@pytest.mark.asyncio
async def test_order_reference_supplied_but_not_found_still_completes():
    result = await _run(customer_found=True, order_found=False, order_reference="SO-9999")
    assert result["status"] == "completed"
    output = result["state"]["node_outputs"]["end_resolution"]["result"]
    assert output["order_found"] is False
    assert output["order_status"] is None
