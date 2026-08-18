"""Regression coverage for the TEMPLATE_STATICALLY_EMPTY_FIELD preflight
check (app/runtime/preflight.py + app/nodes/base.py's
`preflight_static_output_values` extension point, app/nodes/transform.py's
override).

Root cause this guards: a TransformAgent with no `output_schema` always
returns `parsed: {}` (see app/nodes/transform.py's `run()`,
`if not cfg.output_schema: ... return {"raw": ..., "parsed": {}}`). A
downstream node referencing `{{that_node.parsed}}` as a whole-value template
substitutes a permanently-empty dict — which used to only surface as a
runtime Pydantic validation error deep inside a renderer node (after any
upstream LLM call already ran), e.g.
`HorizonHTMLProposalRendererConfig(content={})`. This is now caught
structurally, at zero token cost, before the workflow ever runs.
"""
from __future__ import annotations

from app.runtime.preflight import preflight_workflow_yaml

_BASE = """
name: Preflight Static Output Test
entry: transform
exit: render
inputs:
  document_title:
    type: text
nodes:
  - id: transform
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      prompt_template: "Summarize: {{{{inputs.document_title}}}}"
{output_schema}
  - id: render
    type: HorizonHTMLProposalRenderer
    config:
      content: '{{{{transform.{field}}}}}'
      content_format: markdown
edges:
  - from: transform
    to: render
"""


def _workflow(*, output_schema: str = "", field: str = "parsed") -> str:
    return _BASE.format(output_schema=output_schema, field=field)


def test_bare_parsed_reference_with_no_output_schema_is_flagged():
    report = preflight_workflow_yaml(_workflow())
    codes = [issue.code for issue in report.errors]
    assert "TEMPLATE_STATICALLY_EMPTY_FIELD" in codes


def test_error_message_names_the_node_and_suggests_raw():
    report = preflight_workflow_yaml(_workflow())
    issue = next(
        i for i in report.errors if i.code == "TEMPLATE_STATICALLY_EMPTY_FIELD"
    )
    assert "transform" in issue.message
    assert "parsed" in issue.message
    assert issue.suggestion is not None and ".raw" in issue.suggestion


def test_referencing_raw_instead_is_not_flagged():
    report = preflight_workflow_yaml(_workflow(field="raw"))
    codes = [issue.code for issue in report.errors]
    assert "TEMPLATE_STATICALLY_EMPTY_FIELD" not in codes


def test_setting_output_schema_makes_bare_parsed_reference_valid():
    """With an output_schema declared, `.parsed` is a real, populated dict,
    not a hardcoded {} -- but a *bare* top-level reference still resolves to
    a dict substituted into a `content: str` field, so this correctly stays
    an error, just a different (pre-existing) one: TEMPLATE type mismatch is
    not statically known here anymore, but Pydantic still catches it at
    runtime construction. What must NOT appear is the STATIC check, since
    the value is no longer provably always {}."""
    schema = """
      output_schema:
        summary: str
"""
    report = preflight_workflow_yaml(_workflow(output_schema=schema))
    codes = [issue.code for issue in report.errors]
    assert "TEMPLATE_STATICALLY_EMPTY_FIELD" not in codes


def test_nested_reference_with_no_output_schema_is_still_flagged_as_empty():
    """Even a nested reference ({{transform.parsed.summary}}) is caught by
    the new, more precise check when output_schema is empty -- "parsed" is
    always {} regardless of which sub-key you ask for, so this is a better
    diagnosis than the generic "undeclared structured field" message."""
    report = preflight_workflow_yaml(_workflow(field="parsed.summary"))
    codes = [issue.code for issue in report.errors]
    assert "TEMPLATE_STATICALLY_EMPTY_FIELD" in codes


def test_pre_existing_undeclared_structured_field_check_still_works():
    """A genuinely different bug: output_schema IS declared (so "parsed" is
    no longer statically-known-empty -- the new check must stay silent) but
    the template references a sub-field that schema never declared. This is
    TEMPLATE_UNKNOWN_STRUCTURED_FIELD's job, unchanged by this session's work."""
    schema = """
      output_schema:
        summary: str
"""
    report = preflight_workflow_yaml(
        _workflow(output_schema=schema, field="parsed.title")
    )
    codes = [issue.code for issue in report.errors]
    assert "TEMPLATE_UNKNOWN_STRUCTURED_FIELD" in codes
    assert "TEMPLATE_STATICALLY_EMPTY_FIELD" not in codes
