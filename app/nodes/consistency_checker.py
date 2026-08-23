"""
ConsistencyChecker — a DETERMINISTIC node (no LLM) that gates the render.

This is change #2. Its power comes entirely from the typed ProposalGraph: it
reads structured fields and asserts hard rules that a prose-parsing check could
never do reliably. It never calls a model, so it is fast, free, and its verdict
is reproducible.

What it enforces (all rules are pass/fail on typed data):
  R1  every KPI has an owner_partner_id, baseline, target, and target_date
  R2  every Objective maps to at least one WorkPackage (objective->WP)
  R3  every WorkPackage has a lead_partner_id
  R4  every WorkPackage.lead_partner_id / partner_ids resolve to a real Partner
  R5  every Task.work_package_id resolves to a real WorkPackage
  R6  every expected-outcome CallRequirement is addressed by some Outcome
  R7  each of the four mandatory compliance dimensions is not MISSING
      (gender, ssh, open_science, ethics) — DNSH tracked but advisory
  R8  every Partner has legal_name + country (submission-readiness)
  R9  no OpenQuestion with blocks_submission=True remains

Verdict:
  - "gate" is one of: PASS | WARN | BLOCK
  - BLOCK if any rule tagged blocking fails; WARN if only advisory rules fail.
The workflow can route on the verdict (e.g. Router node) or simply surface it in
the gap report. It does NOT silently pass.
"""
from __future__ import annotations

from typing import Any

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import Status
from app.proposal_graph.state import proposal_graph_from_state
from pydantic import BaseModel, Field


class ConsistencyCheckerOutput(BaseModel):
    """Validated output for ConsistencyChecker.run() (output_schema ClassVar)."""
    gate: str = "PASS"
    findings: list = Field(default_factory=list)
    report: str = ""


class ConsistencyCheckerInput(BaseModel):
    """Pydantic model defining the ConsistencyCheckerInput shape."""
    pass


class ConsistencyCheckerConfig(BaseModel):
    """Node config — base.__init__ does config_schema(**raw_config)."""
    block_on_warn: bool = False


# (rule_id, description, blocking?)
def _check(graph: ProposalGraph) -> list[dict[str, Any]]:
    """Check the result.

    Args:
        graph (ProposalGraph): Compiled LangGraph graph.

    Returns:
        list[dict[str, Any]]: The result.
    """
    findings: list[dict[str, Any]] = []

    def fail(rule: str, blocking: bool, msg: str) -> None:
        """Compute the fail.

        Args:
            rule (str): The rule.
            blocking (bool): The blocking.
            msg (str): The msg.
        """
        findings.append({"rule": rule, "blocking": blocking, "message": msg})

    partners = graph.partners
    wps = graph.work_packages

    # R1 — KPI completeness
    for kid, k in graph.kpis.items():
        missing = [f for f in ("owner_partner_id", "baseline", "target", "target_date")
                   if not getattr(k, f)]
        if missing:
            fail("R1", True, f"KPI {kid} ({k.name}) missing: {', '.join(missing)}")
        if k.owner_partner_id and k.owner_partner_id not in partners:
            fail("R1", True, f"KPI {kid} owner {k.owner_partner_id} is not a known partner")

    # R2 — objective -> WP
    for oid, o in graph.objectives.items():
        if not o.work_package_ids:
            fail("R2", True, f"Objective {oid} maps to no work package")
        for wid in o.work_package_ids:
            if wid not in wps:
                fail("R2", True, f"Objective {oid} references unknown WP {wid}")

    # R3 / R4 — WP lead + partner resolution
    for wid, wp in wps.items():
        if not wp.lead_partner_id:
            fail("R3", True, f"WorkPackage {wid} ({wp.title}) has no lead partner")
        elif wp.lead_partner_id not in partners:
            fail("R4", True, f"WP {wid} lead {wp.lead_partner_id} is not a known partner")
        for pid in wp.partner_ids:
            if pid not in partners:
                fail("R4", True, f"WP {wid} lists unknown partner {pid}")

    # R5 — task -> WP
    for tid, t in graph.tasks.items():
        if t.work_package_id not in wps:
            fail("R5", True, f"Task {tid} references unknown WP {t.work_package_id}")

    # R6 — expected outcomes covered
    covered_reqs = {o.call_requirement_id for o in graph.outcomes.values()
                    if o.call_requirement_id}
    for rid, req in graph.call_requirements.items():
        if req.kind == "expected_outcome" and rid not in covered_reqs:
            fail("R6", True, f"Expected outcome {rid} is not delivered by any Outcome")

    # R7 — mandatory compliance dimensions
    mandatory = {"gender", "ssh", "open_science", "ethics"}
    present = {c.dimension: c for c in graph.compliance.values()}
    for dim in mandatory:
        c = present.get(dim)
        if c is None or c.status == Status.MISSING:
            fail("R7", True, f"Mandatory compliance dimension '{dim}' is MISSING")
        elif c.status == Status.PARTIAL:
            fail("R7", False, f"Compliance dimension '{dim}' is only PARTIAL: "
                              f"{'; '.join(c.gaps) or 'needs detail'}")
    dnsh = present.get("dnsh")
    if dnsh is None or dnsh.status == Status.MISSING:
        fail("R7", False, "DNSH statement missing (advisory)")

    # R8 — partner identity for submission
    for pid, p in partners.items():
        miss = [f for f in ("legal_name", "country") if not getattr(p, f)]
        if miss:
            fail("R8", False, f"Partner {p.acronym} missing {', '.join(miss)} (submission-readiness)")

    # R9 — blocking open questions
    for qid, q in graph.open_questions.items():
        if q.blocks_submission:
            fail("R9", True, f"Unresolved blocking question {qid}: {q.text}")

    return findings


@NodeRegistry.register
class ConsistencyChecker(NodeType):
    """Deterministic gate. Reads the proposal_graph from state, returns a
    verdict + findings. No model call."""

    type_name = "ConsistencyChecker"

    input_schema = ConsistencyCheckerInput
    config_schema = ConsistencyCheckerConfig
    output_schema = ConsistencyCheckerOutput

    async def run(self, state: dict, config: dict) -> dict:
        """Run the result.

        Args:
            state (dict): Current workflow state.
            config (dict): Node configuration mapping.

        Returns:
            dict: The result.
        """
        graph = proposal_graph_from_state(state)

        findings = _check(graph)
        blocking = [f for f in findings if f["blocking"]]
        warnings = [f for f in findings if not f["blocking"]]

        if blocking:
            gate = "BLOCK"
        elif warnings and config.get("block_on_warn"):
            gate = "BLOCK"
        elif warnings:
            gate = "WARN"
        else:
            gate = "PASS"

        lines = [f"CONSISTENCY / COMPLIANCE CHECK — verdict: {gate}",
                 f"  {len(blocking)} blocking, {len(warnings)} advisory"]
        for f in blocking:
            lines.append(f"  [BLOCK {f['rule']}] {f['message']}")
        for f in warnings:
            lines.append(f"  [WARN  {f['rule']}] {f['message']}")
        report = "\n".join(lines)

        return {
            "gate": gate,
            "findings": findings,
            "report": report,
        }
