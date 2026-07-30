"""Zero-token pipeline validation — never calls a node or LLM.

Validates that every stage's workflow parses and passes its own structural
preflight, that every declared stage input has some resolvable source
(explicit mapping, pipeline-level input, or an earlier stage's node output),
and that any explicit mapping doesn't reference a typo'd input name.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError
import yaml

from app.runtime.pipeline_executor import load_stage_workflow, resolve_input_source
from app.runtime.pipeline_loader import stage_workflow_path
from app.runtime.pipeline_schema import PipelineSpec
from app.runtime.preflight import (
    PreflightCheck,
    PreflightIssue,
    PreflightSeverity,
    preflight_workflow_yaml,
)


class PipelinePreflightReport(BaseModel):
    valid: bool
    pipeline_name: str | None = None
    stage_count: int = 0
    checks: list[PreflightCheck] = Field(default_factory=list)
    issues: list[PreflightIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.severity == PreflightSeverity.ERROR]

    def refresh(self) -> "PipelinePreflightReport":
        self.valid = not self.errors
        return self


class PipelinePreflightError(ValueError):
    def __init__(self, report: PipelinePreflightReport):
        self.report = report
        summary = "; ".join(issue.message for issue in report.errors[:5])
        super().__init__(
            f"Pipeline preflight failed with {len(report.errors)} error(s): "
            f"{summary}"
        )


def _issue(
    report: PipelinePreflightReport,
    code: str,
    message: str,
    *,
    severity: PreflightSeverity = PreflightSeverity.ERROR,
    stage_id: str | None = None,
    suggestion: str | None = None,
) -> None:
    report.issues.append(
        PreflightIssue(
            code=code,
            severity=severity,
            message=message,
            node_id=stage_id,
            suggestion=suggestion,
        )
    )


def preflight_pipeline_yaml(
    yaml_text: str,
    *,
    provided_inputs: dict[str, Any] | None = None,
) -> PipelinePreflightReport:
    report = PipelinePreflightReport(valid=False)

    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        _issue(report, "PIPELINE_YAML_SYNTAX", f"Invalid YAML: {exc}")
        return report.refresh()
    if not isinstance(raw, dict):
        _issue(report, "PIPELINE_YAML_ROOT", "Pipeline YAML must be one top-level mapping.")
        return report.refresh()

    try:
        spec = PipelineSpec.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors(include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            _issue(
                report,
                "PIPELINE_SCHEMA",
                error["msg"],
                suggestion=f"Correct pipeline field: {location}" if location else None,
            )
        return report.refresh()

    report.pipeline_name = spec.name
    report.stage_count = len(spec.stages)

    pipeline_input_names = set(spec.inputs)
    prior_stage_node_ids: list[tuple[str, set[str]]] = []

    for stage in spec.stages:
        before = len(report.issues)
        path = stage_workflow_path(stage.workflow)
        if not path.exists():
            _issue(
                report,
                "PIPELINE_STAGE_WORKFLOW_MISSING",
                f"Stage {stage.id!r} references workflow {stage.workflow!r}, "
                f"which does not exist ({path}).",
                stage_id=stage.id,
                suggestion="Save that workflow first, or fix the stage's workflow name.",
            )
            _add_check(report, f"stage:{stage.id}", before, "Referenced workflow not found.")
            prior_stage_node_ids.append((stage.id, set()))
            continue

        stage_spec, stage_yaml = load_stage_workflow(stage)
        stage_report = preflight_workflow_yaml(stage_yaml, compile_graph=True)
        for issue in stage_report.errors:
            _issue(
                report,
                f"PIPELINE_STAGE_{issue.code}",
                f"Stage {stage.id!r} ({stage.workflow}): {issue.message}",
                stage_id=stage.id,
                suggestion=issue.suggestion,
            )

        declared_inputs = set(stage_spec.inputs)
        for mapped_name in stage.inputs:
            if mapped_name not in declared_inputs:
                _issue(
                    report,
                    "PIPELINE_STAGE_MAPPING_UNKNOWN_INPUT",
                    f"Stage {stage.id!r} maps {mapped_name!r}, but workflow "
                    f"{stage.workflow!r} declares no such input.",
                    stage_id=stage.id,
                    suggestion=f"Available inputs: {', '.join(sorted(declared_inputs)) or '(none)'}.",
                )

        for name, input_spec in stage_spec.inputs.items():
            source_kind, _source_stage = resolve_input_source(
                name, stage, pipeline_input_names, prior_stage_node_ids,
            )
            if source_kind == "unresolved" and input_spec.required:
                _issue(
                    report,
                    "PIPELINE_STAGE_INPUT_UNRESOLVED",
                    f"Stage {stage.id!r} requires input {name!r}, but nothing "
                    "supplies it — no explicit mapping, no pipeline-level "
                    "input of that name, and no earlier stage has a node "
                    "with that id.",
                    stage_id=stage.id,
                    suggestion=(
                        f"Add `{name}` to this pipeline's own inputs, or map "
                        f"it explicitly on stage {stage.id!r}."
                    ),
                )

        _add_check(
            report,
            f"stage:{stage.id}",
            before,
            f"{len(stage_spec.inputs)} declared input(s) checked for a resolvable source.",
        )
        prior_stage_node_ids.append((stage.id, {n.id for n in stage_spec.nodes}))

    if provided_inputs is not None:
        before = len(report.issues)
        for name, input_spec in spec.inputs.items():
            value = provided_inputs.get(name)
            missing = value is None or value == "" or value == []
            if input_spec.required and missing:
                _issue(
                    report,
                    "PIPELINE_REQUIRED_INPUT_MISSING",
                    f"Required pipeline input {name!r} is missing.",
                )
        _add_check(
            report,
            "pipeline_inputs",
            before,
            f"{len(provided_inputs)} supplied pipeline input(s) checked.",
        )

    return report.refresh()


def _add_check(
    report: PipelinePreflightReport,
    name: str,
    before: int,
    detail: str,
) -> None:
    new_issues = report.issues[before:]
    if any(i.severity == PreflightSeverity.ERROR for i in new_issues):
        status = "failed"
    elif new_issues:
        status = "warning"
    else:
        status = "passed"
    report.checks.append(PreflightCheck(name=name, status=status, detail=detail))


def require_pipeline_preflight(report: PipelinePreflightReport) -> None:
    if not report.valid:
        raise PipelinePreflightError(report)
