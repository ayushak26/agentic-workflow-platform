from __future__ import annotations

from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import preflight_workflow_yaml
from app.workflow.library_metadata import (
    infer_output_types,
    library_summary,
    readiness_summary,
)


LEGACY_YAML = """
name: legacy_docx_workflow
description: Turns a draft into a reviewed DOCX.
nodes:
  - id: draft
    type: Literal
    config:
      value: hello
  - id: review
    type: HumanInLoopAgent
    config:
      question: Approve?
      allowed_actions: [approve]
  - id: render
    type: DOCXProposalRenderer
    config: {}
edges:
  - from: draft
    to: review
  - from: review
    to: render
entry: draft
exit: render
"""

DECLARED_YAML = """
name: declared_workflow
description: Fallback description.
library:
  title: Custom Title
  summary: Custom summary text.
  purpose: [research]
  outputs: [pdf]
  visibility_status: approved
  human_reviews:
    count: 1
    labels: ["Approve draft"]
nodes:
  - id: only
    type: Literal
    config:
      value: ok
edges: []
"""


def test_legacy_workflow_gets_honest_fallback_summary():
    spec = load_workflow_from_string(LEGACY_YAML)
    summary = library_summary(spec)

    assert summary["declared"] is False
    assert summary["title"] == "Legacy docx workflow"
    assert summary["summary"] == "Turns a draft into a reviewed DOCX."
    assert summary["visibility_status"] == "draft"
    assert summary["human_reviews"]["count"] == 1  # one HumanInLoopAgent node
    assert summary["outputs"] == ["docx"]


def test_workflow_with_no_description_gets_not_yet_provided_summary():
    spec = load_workflow_from_string(
        """
name: bare
nodes:
  - id: only
    type: Literal
    config:
      value: ok
edges: []
"""
    )
    summary = library_summary(spec)
    assert summary["summary"] == "Description not yet provided."


def test_declared_library_metadata_is_authoritative():
    spec = load_workflow_from_string(DECLARED_YAML)
    summary = library_summary(spec)

    assert summary["declared"] is True
    assert summary["title"] == "Custom Title"
    assert summary["summary"] == "Custom summary text."
    assert summary["outputs"] == ["pdf"]
    assert summary["visibility_status"] == "approved"
    assert summary["human_reviews"] == {"count": 1, "labels": ["Approve draft"]}


def test_infer_output_types_looks_at_terminal_nodes_only():
    spec = load_workflow_from_string(LEGACY_YAML)
    assert infer_output_types(spec) == ["docx"]


def test_infer_output_types_returns_empty_when_no_hint_matches():
    spec = load_workflow_from_string(
        """
name: no_render
nodes:
  - id: only
    type: Literal
    config:
      value: ok
edges: []
"""
    )
    assert infer_output_types(spec) == []


def test_readiness_summary_ready_for_valid_workflow():
    valid_yaml = """
name: simple_valid_workflow
nodes:
  - id: only
    type: Literal
    config:
      value: ok
edges: []
"""
    report = preflight_workflow_yaml(valid_yaml, compile_graph=False)
    readiness = readiness_summary(report)
    assert readiness["level"] == "ready"
    assert readiness["items"] == []


def test_readiness_summary_blocked_reuses_plain_language_message():
    broken = LEGACY_YAML.replace("type: DOCXProposalRenderer", "type: NotARealNodeType")
    report = preflight_workflow_yaml(broken, compile_graph=False)
    readiness = readiness_summary(report)
    assert readiness["level"] == "blocked"
    assert readiness["items"]
    assert readiness["items"][0]["code"] == "UNKNOWN_NODE_TYPE"
    assert "NotARealNodeType" in readiness["items"][0]["message"]


def test_readiness_summary_warning_only_is_ready_with_warnings():
    # A router with no default rule produces a warning, not an error.
    router_yaml = """
name: router_warning
nodes:
  - id: source
    type: Literal
    config:
      value: ok
  - id: route
    type: RouterAgent
    config:
      mode: rule
      rules:
        - name: a
          default: false
  - id: a
    type: Literal
    config:
      value: 1
edges:
  - from: source
    to: route
  - from: route
    condition: route
    branches:
      a: a
entry: source
exit: a
"""
    report = preflight_workflow_yaml(router_yaml, compile_graph=False)
    readiness = readiness_summary(report)
    assert readiness["level"] == "ready_with_warnings"
    assert any(item["code"] == "ROUTER_NO_DEFAULT" for item in readiness["items"])
