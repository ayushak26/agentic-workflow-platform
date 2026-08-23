"""Derives Workflow Library catalog metadata and readiness from a WorkflowSpec.

Every field here is presentation-only — never consulted by the compiler or
executor. When a workflow declares an explicit `library` block (see
`app.runtime.schema.LibraryMetadataSpec`), that block is authoritative. When
it doesn't (every pre-existing workflow), fields are derived deterministically
from the graph structure and node types, or left as an honest "not yet
provided"/`None` rather than guessing information — such as evidence policy
or approval status — that can't be inferred safely from the YAML alone.
"""
from __future__ import annotations

from typing import Any

from app.runtime.preflight import PreflightSeverity, WorkflowPreflightReport
from app.runtime.schema import WorkflowSpec

# Naming heuristic only. No node's `output_schema` encodes the actual file
# format it renders (DOCX/PDF/PPTX renderers all just return a bare
# `minio_key` — confirmed by inspecting docx_renderer.py/pdf_tool.py/
# powerpoint_tool.py), so this looks for a known format hint in the *type
# name* of a terminal node instead. Declare `library.outputs` explicitly on
# a workflow to override this guess.
_OUTPUT_TYPE_HINTS: dict[str, str] = {
    "docx": "docx",
    "word": "docx",
    "pdf": "pdf",
    "powerpoint": "pptx",
    "ppt": "pptx",
    "excel": "xlsx",
    "html": "html",
}


def _humanize(identifier: str) -> str:
    """Internal helper for the humanize step.

    Args:
        identifier (str): The identifier.

    Returns:
        str: The result.
    """
    spaced = identifier.replace("_", " ").replace("-", " ").strip()
    return spaced[:1].upper() + spaced[1:] if spaced else identifier


def _terminal_node_types(spec: WorkflowSpec) -> list[str]:
    """Internal helper for the terminal node types step.

    Args:
        spec (WorkflowSpec): Parsed workflow specification.

    Returns:
        list[str]: The node types.
    """
    sources = {edge.from_ for edge in spec.edges}
    terminal = [node.type for node in spec.nodes if node.id not in sources]
    return terminal or [node.type for node in spec.nodes]


def infer_output_types(spec: WorkflowSpec) -> list[str]:
    """Compute the infer output types.

    Args:
        spec (WorkflowSpec): Parsed workflow specification.

    Returns:
        list[str]: The output types.
    """
    found: list[str] = []
    for type_name in _terminal_node_types(spec):
        lowered = type_name.lower()
        for hint, output_type in _OUTPUT_TYPE_HINTS.items():
            if hint in lowered and output_type not in found:
                found.append(output_type)
    return found


def _default_human_review_count(spec: WorkflowSpec) -> int:
    """Internal helper for the default human review count step.

    Args:
        spec (WorkflowSpec): Parsed workflow specification.

    Returns:
        int: The human review count.
    """
    return sum(1 for node in spec.nodes if node.type == "HumanInLoopAgent")


def library_summary(spec: WorkflowSpec) -> dict[str, Any]:
    """The Library card/list-facing metadata dict for one workflow."""
    lib = spec.library
    default_reviews = _default_human_review_count(spec)

    if lib is None:
        return {
            "title": _humanize(spec.name),
            "summary": spec.description or "Description not yet provided.",
            "purpose": [],
            "suitable_for": [],
            "not_suitable_for": [],
            "outputs": infer_output_types(spec),
            "input_types": [],
            "typical_duration": None,
            "human_reviews": {"count": default_reviews, "labels": []},
            "evidence_policy": None,
            "visibility_status": "draft",
            "owner_team": None,
            "declared": False,
        }

    return {
        "title": lib.title or spec.name,
        "summary": lib.summary or (spec.description or "Description not yet provided."),
        "purpose": lib.purpose,
        "suitable_for": lib.suitable_for,
        "not_suitable_for": lib.not_suitable_for,
        "outputs": lib.outputs or infer_output_types(spec),
        "input_types": lib.input_types,
        "typical_duration": (
            {
                "minimum_minutes": lib.typical_duration.minimum_minutes,
                "maximum_minutes": lib.typical_duration.maximum_minutes,
            }
            if lib.typical_duration
            else None
        ),
        "human_reviews": (
            {"count": lib.human_reviews.count, "labels": lib.human_reviews.labels}
            if lib.human_reviews
            else {"count": default_reviews, "labels": []}
        ),
        "evidence_policy": (
            {
                "drafting_requires_verified_evidence": (
                    lib.evidence_policy.drafting_requires_verified_evidence
                ),
                "deep_research_is_context_only": (
                    lib.evidence_policy.deep_research_is_context_only
                ),
            }
            if lib.evidence_policy
            else None
        ),
        "visibility_status": lib.visibility_status,
        "owner_team": lib.owner_team,
        "declared": True,
    }


def readiness_summary(report: WorkflowPreflightReport) -> dict[str, Any]:
    """Maps a structural preflight report to the Library's three-state
    readiness model (ready / ready_with_warnings / blocked), reusing the
    report's own plain-language issue text (`message`/`suggestion`) instead
    of inventing new copy or surfacing only a technical code."""
    errors = [
        issue for issue in report.issues if issue.severity == PreflightSeverity.ERROR
    ]
    warnings = [
        issue for issue in report.issues if issue.severity == PreflightSeverity.WARNING
    ]
    if errors:
        level = "blocked"
    elif warnings:
        level = "ready_with_warnings"
    else:
        level = "ready"

    items = [
        {
            "severity": issue.severity.value,
            "code": issue.code,
            "message": issue.message,
            "suggestion": issue.suggestion,
        }
        for issue in [*errors, *warnings]
    ]
    return {"level": level, "items": items}
