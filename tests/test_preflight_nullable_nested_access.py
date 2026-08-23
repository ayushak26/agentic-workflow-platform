"""Coverage for TEMPLATE_NULLABLE_NESTED_ACCESS.

The last remaining "Template path not resolvable" shape that neither the
graph-ordering checks nor the compiler's default-materialisation fix closes:
a template traverses INTO a field whose declared type permits None
(``{{gate.content.text}}`` where ``content: HITLReviewContent | None``).

The compiler now materialises declared defaults, so the key is always present
in node_outputs — but a None default still cannot be indexed, so _lookup
fails with "... <not a dict: NoneType=None>". Real instance:
HumanInLoopAgent's ``content`` is None when no configured context field
resolves to a value (see _review_content's final ``return None``), and
horizon_partb_evidence.yaml reads ``{{select_concept.content.text}}``.

Deliberately a WARNING: whether the field is None depends on runtime data and
these references are usually correct, so erroring would reject valid shipped
workflows — but staying silent hides a hard-to-diagnose mid-run failure.
"""
from __future__ import annotations

from pathlib import Path

from app.runtime.preflight import (
    PreflightSeverity,
    _annotation_permits_none,
    preflight_workflow_yaml,
)

NESTED_ON_NULLABLE = """
name: nullable_nested_access
entry: seed
nodes:
  - id: seed
    type: Literal
    config:
      value: hello
  - id: gate
    type: HumanInLoopAgent
    config:
      question: Approve?
      allowed_actions: [approve, reject]
      context_fields: ["seed.value"]
  - id: consumer
    type: Echo
    config:
      template: "REVIEWED: {{gate.content.text}}"
edges:
  - from: seed
    to: gate
  - from: gate
    to: consumer
exit: consumer
"""

BARE_NULLABLE_REFERENCE = NESTED_ON_NULLABLE.replace(
    "{{gate.content.text}}", "{{gate.content}}"
)

NESTED_ON_NON_NULLABLE = NESTED_ON_NULLABLE.replace(
    "{{gate.content.text}}", "{{gate.decision}}"
)


def _codes(yaml_text: str) -> list[str]:
    return [i.code for i in preflight_workflow_yaml(yaml_text).issues]


class TestAnnotationDetection:
    def test_optional_annotations_are_detected(self):
        assert _annotation_permits_none(str | None)
        assert _annotation_permits_none(int | None)
        assert _annotation_permits_none(dict[str, str] | None)
        assert _annotation_permits_none(None)
        assert _annotation_permits_none(type(None))

    def test_non_optional_annotations_are_not_flagged(self):
        assert not _annotation_permits_none(str)
        assert not _annotation_permits_none(int)
        assert not _annotation_permits_none(dict[str, str])
        assert not _annotation_permits_none(list[str])

    def test_any_is_not_treated_as_nullable(self):
        """`Any` makes no claim about nullability, so flagging it would be
        noise on every loosely-typed output field."""
        from typing import Any

        assert not _annotation_permits_none(Any)


class TestNestedAccessOnNullableField:
    def test_traversing_a_nullable_field_is_flagged(self):
        assert "TEMPLATE_NULLABLE_NESTED_ACCESS" in _codes(NESTED_ON_NULLABLE)

    def test_it_is_a_warning_and_does_not_invalidate_the_workflow(self):
        report = preflight_workflow_yaml(NESTED_ON_NULLABLE)
        issue = next(
            i for i in report.issues
            if i.code == "TEMPLATE_NULLABLE_NESTED_ACCESS"
        )
        assert issue.severity is PreflightSeverity.WARNING
        assert report.valid, [i.message for i in report.errors]

    def test_the_message_names_the_node_and_the_nullable_field(self):
        report = preflight_workflow_yaml(NESTED_ON_NULLABLE)
        issue = next(
            i for i in report.issues
            if i.code == "TEMPLATE_NULLABLE_NESTED_ACCESS"
        )
        assert "gate" in issue.message
        assert "content" in issue.message
        assert issue.suggestion is not None

    def test_a_bare_reference_to_the_same_field_is_not_flagged(self):
        """Only traversal is unsafe — substituting the whole (possibly None)
        value is a legitimate thing to do."""
        assert "TEMPLATE_NULLABLE_NESTED_ACCESS" not in _codes(
            BARE_NULLABLE_REFERENCE
        )

    def test_referencing_a_non_nullable_field_is_not_flagged(self):
        assert "TEMPLATE_NULLABLE_NESTED_ACCESS" not in _codes(
            NESTED_ON_NON_NULLABLE
        )


class TestShippedWorkflows:
    def test_no_shipped_workflow_is_invalidated_by_this_check(self):
        for path in sorted(Path("workflows").glob("*.yaml")):
            report = preflight_workflow_yaml(path.read_text())
            assert report.valid, (
                f"{path.name} became invalid: "
                + "; ".join(i.message for i in report.errors)
            )

    def test_the_known_real_instance_is_optional_and_exempt(self):
        """horizon_partb_evidence.yaml reads {{select_concept.content.text?}}.
        The gate always populates content from its own editable_content_field,
        and the `?` marker makes a None resolution defined behaviour (the
        resolver substitutes None instead of crashing), so the nullable
        traversal warning is exempt for optional references. The unsafe
        non-optional variant must still be surfaced."""
        yaml_text = Path("workflows/horizon_partb_evidence.yaml").read_text()
        report = preflight_workflow_yaml(yaml_text)
        hits = [
            i for i in report.issues
            if i.code == "TEMPLATE_NULLABLE_NESTED_ACCESS"
        ]
        assert not hits, "optional references are exempt by design"

        # Drop the `?` and the same reference becomes unsafe again — the
        # check itself must still catch it.
        unsafe = yaml_text.replace(
            "{{select_concept.content.text?}}",
            "{{select_concept.content.text}}",
        )
        assert unsafe != yaml_text
        unsafe_hits = [
            i for i in preflight_workflow_yaml(unsafe).issues
            if i.code == "TEMPLATE_NULLABLE_NESTED_ACCESS"
        ]
        assert unsafe_hits, "the non-optional variant must still warn"
        assert any("select_concept" in i.message for i in unsafe_hits)
