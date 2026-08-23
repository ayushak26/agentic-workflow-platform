from __future__ import annotations

import pytest

from app.runtime.loader import load_workflow_from_string
from app.workflow.chat_output_compatibility import analyze_chat_output


def compatibility(nodes: str, *, exit_node: str):
    spec = load_workflow_from_string(f"""
name: Chat output test
nodes:
{nodes}
edges: []
entry: {exit_node}
exit: {exit_node}
""")
    return analyze_chat_output(spec)


@pytest.mark.parametrize(("config", "kind"), [
    ("mode: chat_response\n      chat_message: Hello", "text"),
    ("mode: chat_response\n      chat_message: |\n        ```python\n        print('hi')\n        ```", "code"),
    ("mode: custom_response\n      message: Complete", "text"),
    ("mode: workflow_result\n      outputs:\n        - key: result\n          value_from: '{{inputs.value}}'", "text"),
])
def test_end_agent_visible_outputs(config: str, kind: str):
    result = compatibility(
        f"  - id: end\n    type: EndAgent\n    config:\n      {config}",
        exit_node="end",
    )
    assert result.supported is True
    assert kind in result.detected_types


def test_empty_end_agent_is_not_chat_compatible():
    result = compatibility(
        "  - id: end\n    type: EndAgent\n    config:\n      mode: chat_response\n      chat_message: ''",
        exit_node="end",
    )
    assert result.supported is False
    assert result.detected_types == []


@pytest.mark.parametrize(("node_type", "kind"), [
    ("OpenAIImageGenerationAgent", "image"),
    ("PDFProposalRenderer", "pdf"),
    ("DOCXProposalRenderer", "docx"),
    ("HorizonDOCXProposalRenderer", "docx"),
    ("PowerPointProposalSlides", "pptx"),
])
def test_artifact_node_types_are_detected_without_executing(node_type: str, kind: str):
    # Analyze the parsed contract directly; config validity remains the
    # ordinary preflight gate owned by the API before this analyzer runs.
    spec = load_workflow_from_string(f"""
name: Artifact test
nodes:
  - id: artifact
    type: {node_type}
    config: {{}}
edges: []
entry: artifact
exit: artifact
""")
    result = analyze_chat_output(spec)
    assert kind in result.detected_types


def test_structured_terminal_node_uses_readable_text_fallback():
    result = compatibility(
        "  - id: result\n    type: Literal\n    config:\n      value:\n        count: 2",
        exit_node="result",
    )
    assert result.detected_types == ["text"]
    assert result.fallback_to_text is True
    assert result.warnings