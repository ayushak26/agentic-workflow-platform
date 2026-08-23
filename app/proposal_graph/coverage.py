"""Deterministic Horizon call-coverage matrix."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import EvidenceStance, Status


BLOCKING_KINDS = {"hard_eligibility", "expected_outcome", "must_address"}


class CallCoverageRow(BaseModel):
    """Pydantic model defining the CallCoverageRow shape.

    Attributes:
        requirement_id (str).
        kind (str).
        requirement (str).
        status (Status).
        section (str | None).
        mapped_object_ids (list[str]).
        evidence_claim_ids (list[str]).
        verified_claim_count (int).
    """
    requirement_id: str
    kind: str
    requirement: str
    status: Status
    section: str | None = None
    mapped_object_ids: list[str] = Field(default_factory=list)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    verified_claim_count: int = 0
    missing_items: list[str] = Field(default_factory=list)
    owner_partner_ids: list[str] = Field(default_factory=list)
    blocking: bool = False


class CallCoverageMatrix(BaseModel):
    """Pydantic model defining the CallCoverageMatrix shape.

    Attributes:
        rows (list[CallCoverageRow]).
        addressed (int).
        partial (int).
        missing (int).
        coverage_percent (float).
        blocking_requirement_ids (list[str]).
        submission_blocked (bool).
    """
    rows: list[CallCoverageRow]
    addressed: int
    partial: int
    missing: int
    coverage_percent: float
    blocking_requirement_ids: list[str]
    submission_blocked: bool


def _known_object_ids(graph: ProposalGraph) -> set[str]:
    """Internal helper for the known object ids step.

    Args:
        graph (ProposalGraph): Compiled LangGraph graph.

    Returns:
        set[str]: The object ids.
    """
    known: set[str] = set()
    for name in (
        "claims",
        "objectives",
        "innovations",
        "results",
        "outcomes",
        "impacts",
        "work_packages",
        "tasks",
        "partners",
        "kpis",
        "risks",
        "compliance",
    ):
        known.update(getattr(graph, name))
    return known


def build_call_coverage_matrix(graph: ProposalGraph) -> CallCoverageMatrix:
    """Build the call coverage matrix.

    Args:
        graph (ProposalGraph): Compiled LangGraph graph.

    Returns:
        CallCoverageMatrix: The call coverage matrix.
    """
    known_ids = _known_object_ids(graph)
    rows: list[CallCoverageRow] = []

    for requirement in graph.call_requirements.values():
        mapped = [
            object_id
            for object_id in requirement.addressed_by_ids
            if object_id in known_ids
        ]
        outcome_ids = [
            outcome.id
            for outcome in graph.outcomes.values()
            if outcome.call_requirement_id == requirement.id
        ]
        mapped = list(dict.fromkeys([*mapped, *outcome_ids]))

        evidence_claim_ids = [
            claim_id
            for claim_id in requirement.evidence_claim_ids
            if claim_id in graph.claims
        ]
        verified_claim_ids = []
        contradicted_claim_ids = []
        for claim_id in evidence_claim_ids:
            claim = graph.claims[claim_id]
            relations = [
                graph.claim_evidence[relation_id]
                for relation_id in claim.evidence_relation_ids
                if relation_id in graph.claim_evidence
            ]
            if any(
                item.stance == EvidenceStance.SUPPORTS
                and item.confidence >= 0.72
                for item in relations
            ):
                verified_claim_ids.append(claim_id)
            if any(
                item.stance == EvidenceStance.CONTRADICTS
                and item.confidence >= 0.72
                for item in relations
            ):
                contradicted_claim_ids.append(claim_id)

        missing: list[str] = []
        has_location = bool(requirement.addressed_by_section)
        has_mapping = bool(mapped)
        expected_outcome_ok = (
            requirement.kind != "expected_outcome" or bool(outcome_ids)
        )
        if not has_location:
            missing.append("proposal section")
        if not has_mapping:
            missing.append("mapped objective/result/outcome/WP")
        if requirement.kind == "expected_outcome" and not outcome_ids:
            missing.append("linked project outcome")
        if evidence_claim_ids and len(verified_claim_ids) < len(evidence_claim_ids):
            missing.append("verified supporting evidence")
        if contradicted_claim_ids:
            missing.append("resolve contradictory evidence")

        evidence_ok = (
            not evidence_claim_ids
            or len(verified_claim_ids) == len(evidence_claim_ids)
        )
        complete = (
            has_location
            and has_mapping
            and expected_outcome_ok
            and evidence_ok
            and not contradicted_claim_ids
        )
        if complete:
            status = Status.ADDRESSED
        elif has_location or has_mapping or verified_claim_ids:
            status = Status.PARTIAL
        else:
            status = Status.MISSING

        blocking = (
            requirement.kind in BLOCKING_KINDS
            and status != Status.ADDRESSED
        )
        rows.append(
            CallCoverageRow(
                requirement_id=requirement.id,
                kind=requirement.kind,
                requirement=requirement.text,
                status=status,
                section=requirement.addressed_by_section,
                mapped_object_ids=mapped,
                evidence_claim_ids=evidence_claim_ids,
                verified_claim_count=len(verified_claim_ids),
                missing_items=list(dict.fromkeys(missing)),
                owner_partner_ids=requirement.owner_partner_ids,
                blocking=blocking,
            )
        )

    addressed = sum(row.status == Status.ADDRESSED for row in rows)
    partial = sum(row.status == Status.PARTIAL for row in rows)
    missing = sum(row.status == Status.MISSING for row in rows)
    denominator = len(rows) or 1
    coverage_percent = round(
        100 * (addressed + (0.5 * partial)) / denominator,
        1,
    )
    blockers = [row.requirement_id for row in rows if row.blocking]
    return CallCoverageMatrix(
        rows=rows,
        addressed=addressed,
        partial=partial,
        missing=missing,
        coverage_percent=coverage_percent,
        blocking_requirement_ids=blockers,
        submission_blocked=bool(blockers),
    )
