"""Deterministic final release gate for proposal-engineering workflows."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class ProposalSubmissionGateInput(BaseModel):
    """Pydantic model defining the ProposalSubmissionGateInput shape."""
    pass


class ProposalSubmissionGateConfig(BaseModel):
    """Pydantic model defining the ProposalSubmissionGateConfig shape.

    Attributes:
        proposal_text (str).
        evidence_blockers (str | list[Any]).
        consistency_gate (str).
        consistency_findings (str | list[Any]).
        evaluation_threshold_passed (bool | str).
        evaluation_total_score (float | str).
        evaluation_blockers (str | list[Any]).
        required_headings (list[str]).
    """
    proposal_text: str
    evidence_blockers: str | list[Any] = Field(default_factory=list)
    consistency_gate: str = "PASS"
    consistency_findings: str | list[Any] = Field(default_factory=list)
    evaluation_threshold_passed: bool | str = False
    evaluation_total_score: float | str = 0.0
    evaluation_blockers: str | list[Any] = Field(default_factory=list)
    required_headings: list[str] = Field(
        default_factory=lambda: [
            "excellence",
            "impact",
            "implementation",
        ]
    )
    minimum_proposal_characters: int = Field(
        default=8_000,
        ge=1_000,
        le=2_000_000,
    )
    require_evaluation_pass: bool = True
    block_on_input_needed: bool = True


class ProposalSubmissionGateOutput(BaseModel):
    """Pydantic model defining the ProposalSubmissionGateOutput shape.

    Attributes:
        status (Literal['READY', 'BLOCKED']).
        submission_ready (bool).
        blockers (list[str]).
        warnings (list[str]).
        checks (list[dict[str, Any]]).
        input_needed_count (int).
        proposal_characters (int).
        evaluation_total_score (float).
    """
    status: Literal["READY", "BLOCKED"]
    submission_ready: bool
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    input_needed_count: int = 0
    proposal_characters: int = 0
    evaluation_total_score: float = 0.0
    report: str


def _messages(value: str | list[Any]) -> list[str]:
    """Internal helper for the messages step.

    Args:
        value (str | list[Any]): Value to process.

    Returns:
        list[str]: The result.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    messages: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(
                item.get("message")
                or item.get("reason")
                or item
            ).strip()
        else:
            text = str(item).strip()
        if text:
            messages.append(text)
    return messages


@NodeRegistry.register
class ProposalSubmissionGate(NodeType):
    """Combine hard proposal checks into one auditable release decision.

    This node never calls an LLM. It does not let a high evaluator score hide
    missing evidence, unresolved graph inconsistencies, incomplete sections, or
    visible ``[INPUT NEEDED]`` markers.
    """

    type_name = "ProposalSubmissionGate"
    description = (
        "Deterministically decide whether a proposal is ready for final human "
        "approval or autonomous document export."
    )
    input_schema = ProposalSubmissionGateInput
    config_schema = ProposalSubmissionGateConfig
    output_schema = ProposalSubmissionGateOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the result.

        Args:
            state (dict[str, Any]): Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        del state
        cfg = ProposalSubmissionGateConfig(**resolved_config)
        proposal = cfg.proposal_text.strip()
        lower = proposal.lower()
        evaluation_passed = (
            cfg.evaluation_threshold_passed
            if isinstance(cfg.evaluation_threshold_passed, bool)
            else cfg.evaluation_threshold_passed.strip().lower()
            in {"1", "true", "yes", "passed"}
        )
        evaluation_score = float(cfg.evaluation_total_score)
        blockers: list[str] = []
        warnings: list[str] = []
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, detail: str) -> None:
            """Check the result.

            Args:
                name (str): Workflow or resource name.
                passed (bool): The passed.
                detail (str): The detail.
            """
            checks.append(
                {
                    "name": name,
                    "status": "passed" if passed else "failed",
                    "detail": detail,
                }
            )

        length_ok = len(proposal) >= cfg.minimum_proposal_characters
        check(
            "minimum_content",
            length_ok,
            (
                f"{len(proposal)} characters; minimum "
                f"{cfg.minimum_proposal_characters}."
            ),
        )
        if not length_ok:
            blockers.append(
                "Proposal content is too short for a complete Part B: "
                f"{len(proposal)} characters."
            )

        missing_headings = [
            heading
            for heading in cfg.required_headings
            if not re.search(
                rf"\b{re.escape(heading.lower())}\b",
                lower,
            )
        ]
        check(
            "required_sections",
            not missing_headings,
            (
                "All required sections found."
                if not missing_headings
                else "Missing: " + ", ".join(missing_headings)
            ),
        )
        if missing_headings:
            blockers.append(
                "Required proposal sections are missing: "
                + ", ".join(missing_headings)
            )

        input_needed_count = len(
            re.findall(r"\[\s*INPUT NEEDED\b", proposal, flags=re.IGNORECASE)
        )
        input_markers_ok = (
            input_needed_count == 0 or not cfg.block_on_input_needed
        )
        check(
            "input_needed_markers",
            input_markers_ok,
            f"{input_needed_count} unresolved marker(s).",
        )
        if input_needed_count:
            message = (
                f"Proposal contains {input_needed_count} unresolved "
                "[INPUT NEEDED] marker(s)."
            )
            if cfg.block_on_input_needed:
                blockers.append(message)
            else:
                warnings.append(message)

        evidence = _messages(cfg.evidence_blockers)
        check(
            "evidence_integrity",
            not evidence,
            (
                "No evidence blockers."
                if not evidence
                else f"{len(evidence)} evidence blocker(s)."
            ),
        )
        blockers.extend(
            f"Evidence: {message}" for message in evidence
        )

        consistency = cfg.consistency_gate.strip().upper()
        consistency_ok = consistency == "PASS"
        check(
            "graph_consistency",
            consistency_ok,
            f"Consistency gate: {consistency or 'UNKNOWN'}.",
        )
        consistency_messages = _messages(cfg.consistency_findings)
        if consistency == "BLOCK":
            blockers.append("Proposal knowledge-graph consistency gate is BLOCK.")
            blockers.extend(
                f"Consistency: {message}"
                for message in consistency_messages
            )
        elif consistency != "PASS":
            warnings.append(
                f"Proposal knowledge-graph consistency gate is "
                f"{consistency or 'UNKNOWN'}."
            )
            warnings.extend(
                f"Consistency: {message}"
                for message in consistency_messages
            )

        evaluation_ok = (
            evaluation_passed
            or not cfg.require_evaluation_pass
        )
        check(
            "independent_evaluation",
            evaluation_ok,
            (
                f"Total score {evaluation_score:.2f}; "
                f"threshold passed={evaluation_passed}."
            ),
        )
        if not evaluation_ok:
            blockers.append(
                "Independent Horizon evaluation did not pass its configured "
                f"threshold (total {evaluation_score:.2f})."
            )
        blockers.extend(
            f"Evaluation: {message}"
            for message in _messages(cfg.evaluation_blockers)
        )

        # Stable de-duplication preserves the first, most actionable wording.
        blockers = list(dict.fromkeys(blockers))
        warnings = list(dict.fromkeys(warnings))
        ready = not blockers
        status: Literal["READY", "BLOCKED"] = (
            "READY" if ready else "BLOCKED"
        )
        report_lines = [
            f"PROPOSAL SUBMISSION GATE - {status}",
            f"{len(blockers)} blocker(s), {len(warnings)} warning(s)",
        ]
        report_lines.extend(f"[BLOCK] {item}" for item in blockers)
        report_lines.extend(f"[WARN] {item}" for item in warnings)

        return {
            "status": status,
            "submission_ready": ready,
            "blockers": blockers,
            "warnings": warnings,
            "checks": checks,
            "input_needed_count": input_needed_count,
            "proposal_characters": len(proposal),
            "evaluation_total_score": evaluation_score,
            "report": "\n".join(report_lines),
        }
