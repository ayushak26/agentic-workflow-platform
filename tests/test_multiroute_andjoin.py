"""MULTIROUTE_ANDJOIN_MAY_NOT_FIRE — the safety net for Multi-Route.

A Multi-Route router (RouterAgent selection="multi") may select any subset
of its branches; a downstream node that AND-joins (waits for) two or more of
those branches can silently never fire if the run doesn't select all of
them — confirmed empirically against real langgraph 1.2.9 (no exception, no
timeout, the invocation just completes without ever running that node or
anything downstream of it). This mirrors FANIN_UNREACHABLE_ANDJOIN, which
catches the analogous shape for an ordinary (single-select) router's
mutually-exclusive branches — this is the same failure mode for the
"optional branch" case instead of the "exclusive branch" case.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import app.nodes  # noqa: F401
from app.runtime.preflight import preflight_workflow_yaml

CONDITIONS_HEADER = """
name: multiroute_test
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
"""

BASE_EDGES = """
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


def _issue_codes(yaml_text: str) -> set[str]:
    report = preflight_workflow_yaml(yaml_text)
    return {issue.code for issue in report.errors}


def test_three_branch_reconvergence_is_flagged():
    """A→D, B→D, C→D from three distinct Multi-Route branches — none of
    them are guaranteed alongside the others, so D can silently never fire."""
    yaml_text = (
        CONDITIONS_HEADER
        + """  - id: compile_reply
    type: Echo
    config:
      template: "{{sales_step.value}} {{engineering_step.value}} {{supply_chain_step.value}}"
"""
        + BASE_EDGES
        + """  - from: sales_step
    to: compile_reply
  - from: engineering_step
    to: compile_reply
  - from: supply_chain_step
    to: compile_reply
"""
    )
    assert "MULTIROUTE_ANDJOIN_MAY_NOT_FIRE" in _issue_codes(yaml_text)


def test_same_branch_reconvergence_is_not_flagged():
    """Two nodes that both depend on the SAME selected branch (not two
    different ones) can safely AND-join — they always run together."""
    yaml_text = """
name: multiroute_same_branch
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
  - id: sales_a
    type: Literal
    config:
      value: a
  - id: sales_b
    type: Literal
    config:
      value: b
  - id: general_step
    type: Literal
    config:
      value: g
  - id: merge_sales
    type: Echo
    config:
      template: "{{sales_a.value}} {{sales_b.value}}"
edges:
  - from: start
    to: triage
  - from: triage
    condition: route
    branches:
      sales: sales_a
      general: general_step
  - from: sales_a
    to: [sales_b, merge_sales]
  - from: sales_b
    to: merge_sales
"""
    assert "MULTIROUTE_ANDJOIN_MAY_NOT_FIRE" not in _issue_codes(yaml_text)


def test_branch_plus_unrelated_plain_predecessor_is_flagged():
    """A Multi-Route branch target that ALSO has an unconditional plain
    predecessor is the join-gate shape: the plain predecessor always runs,
    the branch target may not — reconverging is just as unsafe."""
    yaml_text = (
        CONDITIONS_HEADER
        + """  - id: always_runs
    type: Literal
    config:
      value: p
  - id: compile_reply
    type: Echo
    config:
      template: "{{always_runs.value}} {{sales_step.value}}"
"""
        + BASE_EDGES
        + """  - from: start
    to: always_runs
  - from: always_runs
    to: compile_reply
  - from: sales_step
    to: compile_reply
"""
    )
    assert "MULTIROUTE_ANDJOIN_MAY_NOT_FIRE" in _issue_codes(yaml_text)


def test_two_labels_to_one_target_is_not_flagged():
    """Two branch labels of the SAME Multi-Route edge pointing at the same
    target is one arrival group (the compiler dedupes to one dispatch
    destination — either label reaching it is sufficient), not two
    independent ones that must both fire."""
    yaml_text = """
name: multiroute_shared_target
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
  - id: shared_step
    type: Literal
    config:
      value: shared
  - id: general_step
    type: Literal
    config:
      value: g
edges:
  - from: start
    to: triage
  - from: triage
    condition: route
    branches:
      sales: shared_step
      engineering: shared_step
      general: general_step
"""
    assert "MULTIROUTE_ANDJOIN_MAY_NOT_FIRE" not in _issue_codes(yaml_text)


def test_single_selection_router_regression_uses_the_old_check_not_the_new_one():
    """An ordinary (selection unset -> single) router's mutually-exclusive
    branches reconverging must still trip FANIN_UNREACHABLE_ANDJOIN, not the
    new Multi-Route-specific code — _exclusive_branch_groups must keep
    working for single-select routers exactly as before."""
    yaml_text = """
name: single_select_regression
entry: start
nodes:
  - id: start
    type: Literal
    config:
      value: seed
  - id: triage
    type: RouterAgent
    config:
      mode: field
      route_field: start.value
      branches:
        seed: a_route
        other: b_route
      fallback: a_route
  - id: a_step
    type: Literal
    config:
      value: a
  - id: b_step
    type: Literal
    config:
      value: b
  - id: merge
    type: Echo
    config:
      template: "{{a_step.value}} {{b_step.value}}"
edges:
  - from: start
    to: triage
  - from: triage
    condition: route
    branches:
      a_route: a_step
      b_route: b_step
  - from: a_step
    to: merge
  - from: b_step
    to: merge
"""
    codes = _issue_codes(yaml_text)
    assert "FANIN_UNREACHABLE_ANDJOIN" in codes
    assert "MULTIROUTE_ANDJOIN_MAY_NOT_FIRE" not in codes


def test_every_shipped_workflow_triggers_neither_check():
    """The standing guard: no shipped workflow may rely on a shape either
    fan-in safety check would reject — same guard this repo already runs
    for FANIN_UNREACHABLE_ANDJOIN, extended to cover the new code."""
    failures: list[str] = []
    for path in sorted(Path("workflows").glob("*.yaml")):
        report = preflight_workflow_yaml(path.read_text())
        codes = {issue.code for issue in report.errors}
        bad = codes & {"FANIN_UNREACHABLE_ANDJOIN", "MULTIROUTE_ANDJOIN_MAY_NOT_FIRE"}
        if bad:
            failures.append(f"{path.name}: {sorted(bad)}")
    assert failures == []
