"""Stress tests for the two preflight checks added to fully close out the
"Template path not resolvable" bug class:

- TEMPLATE_CONDITIONAL_UPSTREAM: a template reads a node that's only
  guaranteed on SOME paths to the referencing node, not every path.
- FANIN_UNREACHABLE_ANDJOIN: a plain-edge fan-in target requires two
  predecessors that are provably mutually-exclusive branches of the same
  upstream router — it can never fire at all (a distinct, more severe
  failure mode discovered while stress-testing the first check: it doesn't
  raise a KeyError, it just silently never runs, confirmed against real
  langgraph 1.2.9 execution).

Also includes a standing regression guard: every shipped workflow must
continue to trigger neither check.
"""
from __future__ import annotations

from pathlib import Path

from app.runtime.preflight import preflight_workflow_yaml

ROUTER_BASE = """
name: fanin_safety_test
entry: router_node
inputs:
  x:
    type: text
nodes:
  - id: router_node
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: A
          condition: "inputs.x == 1"
        - name: B
          default: true
  - id: branch_a
    type: Literal
    config:
      value: a-result
  - id: branch_b
    type: Literal
    config:
      value: b-result
  - id: after_merge
    type: Echo
    config:
      template: "{merge_template}"
edges:
  - from: router_node
    condition: route
    branches:
      A: branch_a
      B: branch_b
{merge_edges}
exit: after_merge
"""


def _workflow(*, merge_template: str, merge_edges: str) -> str:
    return ROUTER_BASE.format(merge_template=merge_template, merge_edges=merge_edges)


BOTH_BRANCHES_TO_MERGE = """  - from: branch_a
    to: after_merge
  - from: branch_b
    to: after_merge
"""

ROUTER_DIRECTLY_TO_MERGE = ""  # replaced per-test where router targets after_merge directly


def test_diamond_reconvergence_via_plain_edges_is_flagged_as_unreachable():
    """The actual bug discovered mid-investigation: two mutually exclusive
    router branches reconverging via separate plain edges into a shared
    node makes that node -- and everything downstream -- silently never
    fire. Confirmed against real langgraph execution before this test was
    written (see git history / PR description)."""
    wf = _workflow(merge_template="merged", merge_edges=BOTH_BRANCHES_TO_MERGE)
    report = preflight_workflow_yaml(wf)
    codes = [i.code for i in report.errors]
    assert "FANIN_UNREACHABLE_ANDJOIN" in codes
    assert not report.valid


def test_router_branches_mapping_to_the_same_target_directly_is_fine():
    """Both decisions dispatching directly to the SAME node (not via
    separate intermediate nodes that then plain-edge together) is the
    correct way to express 'either branch converges here' -- must not be
    flagged."""
    wf = """
name: same_target_ok
entry: router_node
inputs:
  x:
    type: text
nodes:
  - id: router_node
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: A
          condition: "inputs.x == 1"
        - name: B
          default: true
  - id: after_merge
    type: Echo
    config:
      template: converged
edges:
  - from: router_node
    condition: route
    branches:
      A: after_merge
      B: after_merge
exit: after_merge
"""
    report = preflight_workflow_yaml(wf)
    codes = [i.code for i in report.errors]
    assert "FANIN_UNREACHABLE_ANDJOIN" not in codes


def test_unconditional_parallel_fanout_is_not_flagged():
    """Two nodes that are both direct successors of a shared, UNCONDITIONAL
    plain fan-out (`to: [a, b]`) are 'incomparable' in the same graph-theory
    sense as mutually-exclusive router branches, but they DO always run
    together -- must not be flagged as mutually exclusive. This is
    deliberately the common case (confirmed: dozens of legitimate hits
    across this repo's own shipped workflows use exactly this pattern for
    parallel section drafting)."""
    wf = """
name: parallel_fanout_ok
entry: start
nodes:
  - id: start
    type: Literal
    config:
      value: seed
  - id: draft_a
    type: Literal
    config:
      value: a
  - id: draft_b
    type: Literal
    config:
      value: b
  - id: compile
    type: Echo
    config:
      template: "{{draft_a.value}}-{{draft_b.value}}"
edges:
  - from: start
    to: [draft_a, draft_b]
  - from: draft_a
    to: compile
  - from: draft_b
    to: compile
exit: compile
"""
    report = preflight_workflow_yaml(wf)
    codes = [i.code for i in report.errors]
    assert "FANIN_UNREACHABLE_ANDJOIN" not in codes
    assert "TEMPLATE_CONDITIONAL_UPSTREAM" not in codes
    assert report.valid


def test_reference_to_sibling_branch_node_is_caught_by_existing_not_upstream_check():
    """A node on branch A referencing branch B's output directly (not via a
    merge point) is already caught by TEMPLATE_NOT_UPSTREAM, since siblings
    under a router share no forward path at all -- confirms the new checks
    don't need to duplicate that coverage."""
    wf = """
name: cross_branch_direct
entry: router_node
inputs:
  x:
    type: text
nodes:
  - id: router_node
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: A
          condition: "inputs.x == 1"
        - name: B
          default: true
  - id: branch_a
    type: Echo
    config:
      template: "{{branch_b.value}}"
  - id: branch_b
    type: Literal
    config:
      value: b-result
edges:
  - from: router_node
    condition: route
    branches:
      A: branch_a
      B: branch_b
exit: branch_a
"""
    report = preflight_workflow_yaml(wf)
    codes = [i.code for i in report.errors]
    assert "TEMPLATE_NOT_UPSTREAM" in codes


def test_every_shipped_workflow_triggers_neither_new_check():
    """Standing regression guard -- if a future workflow edit introduces
    either failure mode, this fails immediately at zero token cost instead
    of surfacing as a runtime KeyError or a silently-truncated run."""
    failures: list[str] = []
    for path in sorted(Path("workflows").glob("*.yaml")):
        report = preflight_workflow_yaml(path.read_text())
        hits = [
            i
            for i in report.errors
            if i.code in ("FANIN_UNREACHABLE_ANDJOIN", "TEMPLATE_CONDITIONAL_UPSTREAM")
        ]
        if hits:
            failures.append(
                f"{path.name}: " + "; ".join(f"{i.code}: {i.message}" for i in hits)
            )
    assert failures == []
