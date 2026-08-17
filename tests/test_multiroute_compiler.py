"""Compiler-level regression test for Multi-Route (RouterAgent
selection="multi") — compiles and actually invokes a real graph through
LangGraph, asserting the selected subset of branches ran and the unselected
branch did not. Mirrors tests/test_hitl_mixed_fanin.py's shape: this is
about proving real execution behavior, not just static preflight shape.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string

MULTIROUTE_WORKFLOW = """
name: multiroute_repro
entry: start
nodes:
  - id: start
    type: Literal
    config:
      value: seed
  - id: triage
    type: RouterAgent
    config:
      mode: conditions
      selection: multi
      fallback: general
      cases:
        - route: sales
          when:
            operator: and
            conditions:
              - field: start.value
                operator: equals
                value: seed
        - route: engineering
          when:
            operator: and
            conditions:
              - field: start.value
                operator: equals
                value: seed
        - route: supply_chain
          when:
            operator: and
            conditions:
              - field: start.value
                operator: equals
                value: not-seed
  - id: sales_step
    type: Literal
    config:
      value: sales-done
  - id: engineering_step
    type: Literal
    config:
      value: engineering-done
  - id: supply_chain_step
    type: Literal
    config:
      value: supply-chain-done
  - id: general_step
    type: Literal
    config:
      value: general-done
edges:
  - from: start
    to: triage
  - from: triage
    condition: route
    branches:
      sales: sales_step
      engineering: engineering_step
      supply_chain: supply_chain_step
      general: general_step
"""


async def test_multiroute_runs_exactly_the_selected_branches():
    spec = load_workflow_from_string(MULTIROUTE_WORKFLOW)
    services = {"langgraph_checkpointer": MemorySaver()}

    result = await run_workflow(spec, {}, services=services, run_id="multiroute-t1")

    assert result["status"] == "completed"
    outputs = result["state"]["node_outputs"]

    assert outputs["triage"]["routes"] == ["sales", "engineering"]
    assert outputs["sales_step"] == {"value": "sales-done"}
    assert outputs["engineering_step"] == {"value": "engineering-done"}
    # The unselected branch and the (unused) fallback branch must genuinely
    # never have run — not merely absent from a projection.
    assert "supply_chain_step" not in outputs
    assert "general_step" not in outputs


async def test_multiroute_falls_back_when_no_case_matches():
    workflow = MULTIROUTE_WORKFLOW.replace("value: seed", "value: nothing-matches", 1)
    spec = load_workflow_from_string(workflow)
    services = {"langgraph_checkpointer": MemorySaver()}

    result = await run_workflow(spec, {}, services=services, run_id="multiroute-t2")

    assert result["status"] == "completed"
    outputs = result["state"]["node_outputs"]
    assert outputs["triage"]["routes"] == ["general"]
    assert outputs["triage"]["used_fallback"] is True
    assert outputs["general_step"] == {"value": "general-done"}
    assert "sales_step" not in outputs
    assert "engineering_step" not in outputs
    assert "supply_chain_step" not in outputs
