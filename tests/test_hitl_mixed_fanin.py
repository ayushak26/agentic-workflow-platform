"""Regression test for a compiler bug in app/runtime/compiler.py's
_wire_edges: a target reached by BOTH a HITL-gated predecessor chain AND a
plain (non-HITL) predecessor used to fire as soon as the FASTER plain
predecessor completed, without waiting for the HITL branch to even pause —
let alone resume. Confirmed via a minimal raw-LangGraph reproduction
(langgraph 1.2.9) that a target reached via two separately-registered
incoming edges (whether add_edge+add_edge or add_conditional_edges+add_edge)
races rather than AND-joins the instant one branch involves a genuine
interrupt()-based pause+resume — this is invisible with a synchronous
router (which is presumably why it went unnoticed) and only shows up with
real human-in-the-loop timing.

Real-world trigger: workflows/horizon_partb_evidence.yaml's
`normalise_source_graph` node, reached both via a HITL gate
(`approve_call_interpretation`) and a fast plain path (`partb_metadata`) —
this used to fail with `KeyError: Template path not resolvable:
call_intelligence.parsed` because normalise_source_graph fired before
call_intelligence (several hops behind the HITL gate) had even run.

The fix: a synthetic pass-through "join gate" node that a HITL router
dispatches to instead of a shared target directly, combined with the
target's other predecessors into a single add_edge([...], target) call —
see _wire_edges' step 3/5.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow_durable
from app.runtime.loader import load_workflow_from_string

MIXED_FANIN_WORKFLOW = """
name: fanin_repro
entry: start
nodes:
  - id: start
    type: Literal
    config:
      value: seed
  - id: call_intelligence
    type: Literal
    config:
      value: call-intel-result
  - id: approve_gate
    type: HumanInLoopAgent
    config:
      question: Approve?
      allowed_actions: [approve, reject]
  - id: partb_metadata
    type: Literal
    config:
      value: fast-meta
  - id: normalise
    type: Echo
    config:
      template: "{{call_intelligence.value}}-{{partb_metadata.value}}"
edges:
  - from: start
    to: [call_intelligence, partb_metadata]
  - from: call_intelligence
    to: approve_gate
  - from: approve_gate
    to: normalise
  - from: partb_metadata
    to: normalise
exit: normalise
"""


async def test_shared_target_waits_for_hitl_branch_not_just_fast_sibling():
    spec = load_workflow_from_string(MIXED_FANIN_WORKFLOW)
    services = {"langgraph_checkpointer": MemorySaver()}

    result = await run_workflow(spec, {}, services=services, run_id="fanin-t1")

    assert result["status"] == "paused"
    outputs = result.get("state", {}).get("node_outputs", {})
    # The fast plain sibling (partb_metadata) has completed, but the shared
    # target must NOT have fired yet -- it's still waiting on the paused
    # HITL branch.
    assert "partb_metadata" in outputs
    assert "normalise" not in outputs, (
        "regression: normalise fired before the HITL branch resumed"
    )


async def test_shared_target_fires_correctly_after_resume_with_both_inputs():
    spec = load_workflow_from_string(MIXED_FANIN_WORKFLOW)
    services = {"langgraph_checkpointer": MemorySaver()}

    await run_workflow(spec, {}, services=services, run_id="fanin-t2")
    resumed = await resume_workflow_durable(
        "fanin-t2", {"decision": "approve"}, services=services
    )

    assert resumed["status"] == "completed"
    outputs = resumed.get("state", {}).get("node_outputs", {})
    # Both branches' values made it into the shared target's template --
    # proof the join is a genuine AND, not a race that happened to land
    # correctly.
    assert outputs["normalise"]["text"] == "call-intel-result-fast-meta"


async def test_rejecting_the_hitl_gate_never_reaches_the_shared_target():
    spec = load_workflow_from_string(MIXED_FANIN_WORKFLOW)
    services = {"langgraph_checkpointer": MemorySaver()}

    await run_workflow(spec, {}, services=services, run_id="fanin-t3")
    resumed = await resume_workflow_durable(
        "fanin-t3", {"decision": "reject"}, services=services
    )

    assert resumed["status"] == "rejected"
    outputs = resumed.get("state", {}).get("node_outputs", {})
    assert "normalise" not in outputs


async def test_horizon_partb_evidence_workflow_passes_structural_preflight():
    """The real workflow that surfaced this bug -- confirms it still
    structurally validates after the compiler fix (this check doesn't run
    the graph, just the existing zero-token preflight)."""
    from pathlib import Path

    from app.runtime.preflight import preflight_workflow_yaml

    report = preflight_workflow_yaml(
        Path("workflows/horizon_partb_evidence.yaml").read_text()
    )
    assert report.valid, [i.message for i in report.errors]


# ── Additional topology stress: HITL behind a router, and two HITL gates
# sharing one target ──────────────────────────────────────────────────────

ROUTER_BEHIND_HITL_WORKFLOW = """
name: router_fanin_repro
entry: start
nodes:
  - id: start
    type: Literal
    config:
      value: seed
  - id: call_intelligence
    type: Literal
    config:
      value: call-intel-result
  - id: approve_gate
    type: HumanInLoopAgent
    config:
      question: Approve?
      allowed_actions: [approve, reject]
  - id: router_node
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: GO_SHARED
          default: true
  - id: partb_metadata
    type: Literal
    config:
      value: fast-meta
  - id: shared_target
    type: Echo
    config:
      template: "{{call_intelligence.value}}-{{partb_metadata.value}}"
edges:
  - from: start
    to: [call_intelligence, partb_metadata]
  - from: call_intelligence
    to: approve_gate
  - from: approve_gate
    to: router_node
  - from: router_node
    condition: route
    branches:
      GO_SHARED: shared_target
  - from: partb_metadata
    to: shared_target
exit: shared_target
"""


async def test_router_downstream_of_hitl_still_waits_for_plain_sibling():
    """A HITL gate feeding a ROUTER (not directly the shared target) whose
    branch reconverges with a plain sibling -- one hop further removed than
    the direct-HITL case, confirming the join-gate fix generalizes past the
    single-hop scenario."""
    spec = load_workflow_from_string(ROUTER_BEHIND_HITL_WORKFLOW)
    services = {"langgraph_checkpointer": MemorySaver()}

    result = await run_workflow(spec, {}, services=services, run_id="router-fanin-t1")
    assert result["status"] == "paused"
    outputs = result.get("state", {}).get("node_outputs", {})
    assert "partb_metadata" in outputs
    assert "shared_target" not in outputs

    resumed = await resume_workflow_durable(
        "router-fanin-t1", {"decision": "approve"}, services=services
    )
    assert resumed["status"] == "completed"
    outputs = resumed.get("state", {}).get("node_outputs", {})
    assert outputs["shared_target"]["text"] == "call-intel-result-fast-meta"


HITL_PLUS_ROUTER_NO_PLAIN_WORKFLOW = """
name: hitl_and_router_fanin_repro
entry: start
nodes:
  - id: start
    type: Literal
    config:
      value: seed
  - id: call_intelligence
    type: Literal
    config:
      value: a-result
  - id: approve_gate
    type: HumanInLoopAgent
    config:
      question: Approve?
      allowed_actions: [approve, reject]
  - id: router_source
    type: Literal
    config:
      value: b-result
  - id: router_b
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: GO
          default: true
  - id: shared_target
    type: Echo
    config:
      template: "{{call_intelligence.value}}-{{router_source.value}}"
edges:
  - from: start
    to: [call_intelligence, router_source]
  - from: call_intelligence
    to: approve_gate
  - from: approve_gate
    to: shared_target
  - from: router_source
    to: router_b
  - from: router_b
    condition: route
    branches:
      GO: shared_target
exit: shared_target
"""


async def test_hitl_join_gate_and_router_join_gate_combine_for_one_target():
    """shared_target's two predecessors are BOTH conditional dispatches (one
    HITL, one an ordinary router) with NO plain-edge group at all -- stresses
    that two join gates of DIFFERENT kinds correctly combine into a single
    AND-join, not just the HITL+plain case the original bug report used."""
    spec = load_workflow_from_string(HITL_PLUS_ROUTER_NO_PLAIN_WORKFLOW)
    services = {"langgraph_checkpointer": MemorySaver()}

    result = await run_workflow(spec, {}, services=services, run_id="hitl-router-t1")
    assert result["status"] == "paused"
    outputs = result.get("state", {}).get("node_outputs", {})
    # router_b's own branch runs synchronously (no interrupt), so it's
    # already fired by the time the HITL gate pauses -- but shared_target
    # itself must still wait for the HITL side.
    assert "router_b" in outputs
    assert "shared_target" not in outputs

    resumed = await resume_workflow_durable(
        "hitl-router-t1", {"decision": "approve"}, services=services
    )
    assert resumed["status"] == "completed"
    outputs = resumed.get("state", {}).get("node_outputs", {})
    assert outputs["shared_target"]["text"] == "a-result-b-result"
