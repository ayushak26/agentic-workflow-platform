from __future__ import annotations

import pytest

from app.runtime.pipeline_preflight import preflight_pipeline_yaml

STAGE_ONE_YAML = """
name: pipeline_stage_one
version: "1.0"
nodes:
  - id: greeting
    type: Literal
    config:
      value: {text: "hello"}
entry: greeting
exit: greeting
"""

STAGE_TWO_REQUIRES_GREETING = """
name: pipeline_stage_two
version: "1.0"
inputs:
  greeting:
    type: json
    required: true
nodes:
  - id: repeat_greeting
    type: Literal
    config:
      value: "{{ inputs.greeting }}"
entry: repeat_greeting
exit: repeat_greeting
"""

STAGE_TWO_REQUIRES_UNRELATED = """
name: pipeline_stage_two
version: "1.0"
inputs:
  concept_note:
    type: text
    required: true
nodes:
  - id: repeat
    type: Literal
    config:
      value: "{{ inputs.concept_note }}"
entry: repeat
exit: repeat
"""


@pytest.fixture
def workflows_dir(tmp_path, monkeypatch):
    (tmp_path / "stage_one.yaml").write_text(STAGE_ONE_YAML)
    monkeypatch.setattr("app.runtime.pipeline_loader.WORKFLOWS_DIR", tmp_path)
    return tmp_path


def test_auto_matched_input_passes_preflight(workflows_dir):
    (workflows_dir / "stage_two.yaml").write_text(STAGE_TWO_REQUIRES_GREETING)
    pipeline_yaml = """
name: p
stages:
  - id: evidence
    workflow: stage_one
  - id: drafts
    workflow: stage_two
"""
    report = preflight_pipeline_yaml(pipeline_yaml)
    assert report.valid, [i.message for i in report.errors]


def test_unresolved_required_input_fails_preflight(workflows_dir):
    (workflows_dir / "stage_two.yaml").write_text(STAGE_TWO_REQUIRES_UNRELATED)
    pipeline_yaml = """
name: p
stages:
  - id: evidence
    workflow: stage_one
  - id: drafts
    workflow: stage_two
"""
    report = preflight_pipeline_yaml(pipeline_yaml)
    assert not report.valid
    codes = {i.code for i in report.errors}
    assert "PIPELINE_STAGE_INPUT_UNRESOLVED" in codes


def test_declaring_pipeline_input_resolves_it(workflows_dir):
    (workflows_dir / "stage_two.yaml").write_text(STAGE_TWO_REQUIRES_UNRELATED)
    pipeline_yaml = """
name: p
inputs:
  concept_note:
    type: text
    required: true
stages:
  - id: evidence
    workflow: stage_one
  - id: drafts
    workflow: stage_two
"""
    report = preflight_pipeline_yaml(pipeline_yaml, provided_inputs={"concept_note": "x"})
    assert report.valid, [i.message for i in report.errors]


def test_explicit_mapping_to_unknown_input_is_flagged(workflows_dir):
    (workflows_dir / "stage_two.yaml").write_text(STAGE_TWO_REQUIRES_GREETING)
    pipeline_yaml = """
name: p
stages:
  - id: evidence
    workflow: stage_one
  - id: drafts
    workflow: stage_two
    inputs:
      totally_unknown_field: "{{ evidence.greeting }}"
"""
    report = preflight_pipeline_yaml(pipeline_yaml)
    codes = {i.code for i in report.issues}
    assert "PIPELINE_STAGE_MAPPING_UNKNOWN_INPUT" in codes


def test_missing_stage_workflow_is_flagged(workflows_dir):
    pipeline_yaml = """
name: p
stages:
  - id: evidence
    workflow: does_not_exist
"""
    report = preflight_pipeline_yaml(pipeline_yaml)
    assert not report.valid
    assert any(i.code == "PIPELINE_STAGE_WORKFLOW_MISSING" for i in report.errors)


def test_invalid_pipeline_yaml_reports_syntax_error():
    report = preflight_pipeline_yaml("not: valid: yaml: [")
    assert not report.valid
    assert any(i.code == "PIPELINE_YAML_SYNTAX" for i in report.errors)


def test_duplicate_stage_ids_reported_as_schema_error(workflows_dir):
    pipeline_yaml = """
name: p
stages:
  - id: evidence
    workflow: stage_one
  - id: evidence
    workflow: stage_one
"""
    report = preflight_pipeline_yaml(pipeline_yaml)
    assert not report.valid
    assert any(i.code == "PIPELINE_SCHEMA" for i in report.errors)
