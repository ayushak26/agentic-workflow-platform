"""StartAgent / EndAgent — the canonical entry/exit node types.

Mirrors the WorkflowInputAgent test style in tests/nodes/test_core_primitives.py
(same `run()` helper shape) since StartAgent shares its field-resolution
machinery. Also covers the WorkflowSpec input-derivation validator, the
_project_output End-node fallback, and the new Start/End preflight checks.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.nodes.end import EndAgent
from app.nodes.start import StartAgent
from app.runtime.executor import _project_output
from app.runtime.preflight import preflight_workflow_yaml
from app.runtime.schema import WorkflowSpec


async def run(node: Any, state: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = node.config.model_dump()
    return await node.run(state or {"inputs": {}, "node_outputs": {}}, resolved)


# ---------------------------------------------------------------------------
# StartAgent — input_form mode
# ---------------------------------------------------------------------------

class TestStartInputForm:
    @pytest.mark.asyncio
    async def test_every_declared_field_kind_resolves(self):
        node = StartAgent(
            "begin",
            {
                "mode": "input_form",
                "fields": [
                    {"name": "name", "label": "Name", "type": "string", "source": "inputs.name"},
                    {"name": "notes", "label": "Notes", "type": "text", "source": "inputs.notes"},
                    {"name": "age", "label": "Age", "type": "number", "source": "inputs.age"},
                    {
                        "name": "request_type", "label": "Request Type", "type": "enum",
                        "enum_values": ["technical_support", "sales"], "source": "inputs.request_type",
                    },
                    {
                        "name": "tags", "label": "Tags", "type": "list", "item_type": "enum",
                        "item_enum_values": ["urgent", "billing"], "source": "inputs.tags",
                    },
                    {"name": "subscribe", "label": "Subscribe", "type": "boolean", "source": "inputs.subscribe"},
                    {"name": "due", "label": "Due Date", "type": "date", "source": "inputs.due"},
                ],
            },
        )
        state = {
            "inputs": {
                "name": "Müller Automation GmbH",
                "notes": "Long form text",
                "age": 5,
                "request_type": "technical_support",
                "tags": ["urgent"],
                "subscribe": True,
                "due": "2026-01-01",
            },
            "node_outputs": {},
        }
        output = await run(node, state)
        assert output["data"] == {
            "name": "Müller Automation GmbH",
            "notes": "Long form text",
            "age": 5,
            "request_type": "technical_support",
            "tags": ["urgent"],
            "subscribe": True,
            "due": "2026-01-01",
        }
        assert output["missing"] == []

    @pytest.mark.asyncio
    async def test_file_field_resolves_and_tracks_required_missing(self):
        node = StartAgent(
            "begin",
            {
                "mode": "input_form",
                "fields": [],
                "file_fields": [
                    {"name": "spec_file", "label": "Specification", "required": True, "source": "inputs.spec_file"},
                    {"name": "extra", "label": "Extra", "required": False, "multiple": True, "source": "inputs.extra"},
                ],
            },
        )
        present = await run(node, {
            "inputs": {"spec_file": {"kind": "workflow_file", "file_id": "f1", "name": "spec.pdf"}},
            "node_outputs": {},
        })
        assert present["data"]["spec_file"]["file_id"] == "f1"
        assert present["missing"] == []

        absent = await run(node, {"inputs": {}, "node_outputs": {}})
        assert absent["missing"] == ["spec_file"]

    @pytest.mark.asyncio
    async def test_required_scalar_field_missing_is_reported_not_raised(self):
        node = StartAgent(
            "begin",
            {"fields": [{"name": "question", "label": "Question", "required": True, "source": "inputs.question"}]},
        )
        output = await run(node, {"inputs": {}, "node_outputs": {}})
        assert output["missing"] == ["question"]

    @pytest.mark.asyncio
    async def test_a_value_outside_the_declared_enum_is_rejected(self):
        node = StartAgent(
            "begin",
            {"fields": [{
                "name": "request_type", "label": "Request Type", "type": "enum",
                "enum_values": ["sales"], "source": "inputs.request_type",
            }]},
        )
        with pytest.raises(ValueError):
            await run(node, {"inputs": {"request_type": "not_a_real_value"}, "node_outputs": {}})

    def test_preflight_authorises_each_declared_field(self):
        fields = StartAgent.preflight_output_fields(
            {"mode": "input_form", "fields": [{"name": "question", "label": "Question"}]}
        )
        assert "data.question" in fields


# ---------------------------------------------------------------------------
# StartAgent — chatbot mode
# ---------------------------------------------------------------------------

class TestStartChatbot:
    @pytest.mark.asyncio
    async def test_message_and_attachments_are_read_from_inputs(self):
        node = StartAgent("begin", {"mode": "chatbot", "allow_attachments": True})
        output = await run(node, {
            "inputs": {"message": "Why is our MX-400 overheating?", "attachments": [{"file_id": "f1"}]},
            "node_outputs": {},
        })
        assert output["message"] == "Why is our MX-400 overheating?"
        assert output["attachments"] == [{"file_id": "f1"}]
        assert output["missing"] == []

    @pytest.mark.asyncio
    async def test_missing_message_is_reported(self):
        node = StartAgent("begin", {"mode": "chatbot"})
        output = await run(node, {"inputs": {}, "node_outputs": {}})
        assert output["missing"] == ["message"]

    @pytest.mark.asyncio
    async def test_attachments_disabled_are_never_returned_even_if_supplied(self):
        node = StartAgent("begin", {"mode": "chatbot", "allow_attachments": False})
        output = await run(node, {
            "inputs": {"message": "hi", "attachments": [{"file_id": "f1"}]},
            "node_outputs": {},
        })
        assert output["attachments"] == []

    @pytest.mark.asyncio
    async def test_chatbot_mode_performs_no_business_logic(self):
        """Start only normalizes what came in — no RAG/LLM/routing (§15/§47)."""
        node = StartAgent("begin", {"mode": "chatbot"})
        assert node.required_services({"mode": "chatbot"}) == set()


# ---------------------------------------------------------------------------
# EndAgent
# ---------------------------------------------------------------------------

class TestEndWorkflowResult:
    @pytest.mark.asyncio
    async def test_maps_configured_outputs(self):
        node = EndAgent(
            "finish",
            {"mode": "workflow_result", "outputs": [
                {"key": "answer", "value_from": "The seal kit is SK-8821."},
                {"key": "sources", "value_from": [{"file_name": "guide.pdf"}]},
            ]},
        )
        output = await run(node)
        assert output["result"] == {
            "answer": "The seal kit is SK-8821.",
            "sources": [{"file_name": "guide.pdf"}],
        }


class TestEndCustomResponse:
    @pytest.mark.asyncio
    async def test_title_and_message(self):
        node = EndAgent("finish", {"mode": "custom_response", "title": "Request Completed", "message": "All done."})
        output = await run(node)
        assert output["result"] == {"title": "Request Completed", "message": "All done."}


class TestEndChatResponse:
    @pytest.mark.asyncio
    async def test_plain_reply_has_no_route_or_handoff_keys(self):
        node = EndAgent("finish", {"mode": "chat_response", "chat_message": "The MX-400 shuts down when thermal protection triggers."})
        output = await run(node)
        assert output["result"] == {
            "outcome": "reply",
            "message": "The MX-400 shuts down when thermal protection triggers.",
        }

    @pytest.mark.asyncio
    async def test_route_humanizes_the_label_when_not_configured(self):
        node = EndAgent("finish", {
            "mode": "chat_response", "outcome": "route", "chat_message": "I'll forward this.",
            "route_to": "customer_support",
        })
        output = await run(node)
        assert output["result"]["route_to"] == "customer_support"
        assert output["result"]["route_to_label"] == "Customer Support"

    @pytest.mark.asyncio
    async def test_explicit_route_label_is_not_overridden(self):
        node = EndAgent("finish", {
            "mode": "chat_response", "outcome": "route", "chat_message": "I'll forward this.",
            "route_to": "app_eng", "route_to_label": "Application Engineering",
        })
        output = await run(node)
        assert output["result"]["route_to_label"] == "Application Engineering"

    @pytest.mark.asyncio
    async def test_sources_and_handoff_included_only_when_configured(self):
        bare = await run(EndAgent("finish", {"mode": "chat_response", "chat_message": "hi"}))
        assert "sources" not in bare["result"]
        assert "handoff" not in bare["result"]

        rich = await run(EndAgent("finish", {
            "mode": "chat_response", "chat_message": "hi",
            "sources": [{"file_name": "guide.pdf"}],
            "handoff": {"product": "MX-400", "serial_number": "829193"},
        }))
        assert rich["result"]["sources"] == [{"file_name": "guide.pdf"}]
        assert rich["result"]["handoff"] == {"product": "MX-400", "serial_number": "829193"}


# ---------------------------------------------------------------------------
# WorkflowSpec.derive_inputs_from_start_node
# ---------------------------------------------------------------------------

def _spec(nodes: list[dict], edges: list[dict] | None = None, **extra) -> WorkflowSpec:
    return WorkflowSpec.model_validate({
        "name": "test",
        "nodes": nodes,
        "edges": edges or [],
        **extra,
    })


class TestDeriveInputsFromStartNode:
    def test_required_scalar_field_becomes_a_required_input(self):
        spec = _spec([
            {"id": "begin", "type": "StartAgent", "config": {
                "fields": [{"name": "question", "label": "Question", "required": True}],
            }},
        ])
        assert spec.inputs["question"].required is True
        assert spec.inputs["question"].type == "json"

    def test_file_field_becomes_a_file_input(self):
        spec = _spec([
            {"id": "begin", "type": "StartAgent", "config": {
                "file_fields": [{"name": "spec_file", "label": "Spec", "required": True, "multiple": True, "max_files": 3}],
            }},
        ])
        derived = spec.inputs["spec_file"]
        assert derived.type == "file"
        assert derived.required is True
        assert derived.multiple is True
        assert derived.max_files == 3

    def test_explicit_inputs_declaration_is_never_overridden(self):
        spec = _spec(
            [{"id": "begin", "type": "StartAgent", "config": {
                "fields": [{"name": "question", "label": "Question", "required": True}],
            }}],
            inputs={"question": {"type": "text", "required": False}},
        )
        assert spec.inputs["question"].required is False
        assert spec.inputs["question"].type == "text"

    def test_chatbot_mode_derives_an_attachments_file_input(self):
        spec = _spec([
            {"id": "begin", "type": "StartAgent", "config": {"mode": "chatbot", "allow_attachments": True}},
        ])
        assert spec.inputs["attachments"].type == "file"
        assert spec.inputs["attachments"].multiple is True

    def test_no_start_node_leaves_inputs_untouched(self):
        spec = _spec([{"id": "echo", "type": "Echo", "config": {"template": "hi"}}])
        assert spec.inputs == {}


# ---------------------------------------------------------------------------
# _project_output — End-node fallback
# ---------------------------------------------------------------------------

class TestProjectOutputEndFallback:
    def test_explicit_output_config_still_wins(self):
        spec = _spec(
            [
                {"id": "finish", "type": "EndAgent", "config": {"mode": "workflow_result", "outputs": []}},
            ],
            output={"nodes": [{"node_id": "finish", "flatten": False}]},
        )
        state = {"node_outputs": {"finish": {"result": {"answer": "x"}}}}
        projected = _project_output(spec, state, {})
        assert projected == {"finish": {"result": {"answer": "x"}}}

    def test_single_end_node_is_auto_projected_when_output_is_unset(self):
        spec = _spec([{"id": "finish", "type": "EndAgent", "config": {}}])
        state = {"node_outputs": {"finish": {"result": {"answer": "The seal kit is SK-8821."}}}}
        projected = _project_output(spec, state, {})
        assert projected == {"answer": "The seal kit is SK-8821."}

    def test_no_end_node_projects_nothing(self):
        spec = _spec([{"id": "echo", "type": "Echo", "config": {"template": "hi"}}])
        state = {"node_outputs": {"echo": {"text": "hi"}}}
        assert _project_output(spec, state, {}) is None

    def test_multiple_end_nodes_projects_nothing_automatically(self):
        spec = _spec([
            {"id": "finish_a", "type": "EndAgent", "config": {}},
            {"id": "finish_b", "type": "EndAgent", "config": {}},
        ])
        state = {
            "node_outputs": {
                "finish_a": {"result": {"answer": "a"}},
                "finish_b": {"result": {"answer": "b"}},
            }
        }
        assert _project_output(spec, state, {}) is None


# ---------------------------------------------------------------------------
# Preflight — Start/End edge cardinality
# ---------------------------------------------------------------------------

_BASE_YAML = """
name: test
nodes:
  - id: begin
    type: StartAgent
    config:
      fields:
        - name: question
          label: Question
  - id: echo
    type: Echo
    config:
      template: "{{{{begin.data.question}}}}"
  - id: finish
    type: EndAgent
    config:
      mode: workflow_result
      outputs:
        - key: answer
          value_from: "{{{{echo.text}}}}"
edges:
{edges}
entry: begin
exit: finish
"""


def test_start_with_incoming_edge_is_flagged():
    yaml_text = _BASE_YAML.format(edges="  - from: echo\n    to: begin\n  - from: begin\n    to: echo\n  - from: echo\n    to: finish\n")
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "START_HAS_INCOMING_EDGE" in codes


def test_end_with_outgoing_edge_is_flagged():
    yaml_text = _BASE_YAML.format(edges="  - from: begin\n    to: echo\n  - from: echo\n    to: finish\n  - from: finish\n    to: begin\n")
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "END_HAS_OUTGOING_EDGE" in codes


def test_a_normal_valid_start_end_workflow_has_no_new_issues():
    yaml_text = _BASE_YAML.format(edges="  - from: begin\n    to: echo\n  - from: echo\n    to: finish\n")
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "START_HAS_INCOMING_EDGE" not in codes
    assert "END_HAS_OUTGOING_EDGE" not in codes
    assert "MULTIPLE_START_NODES" not in codes


def test_legacy_workflow_without_start_or_end_has_no_new_issues():
    yaml_text = """
name: legacy
nodes:
  - id: echo
    type: Echo
    config:
      template: "hi"
edges: []
entry: echo
exit: echo
"""
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert not codes & {"START_HAS_INCOMING_EDGE", "END_HAS_OUTGOING_EDGE", "MULTIPLE_START_NODES"}
