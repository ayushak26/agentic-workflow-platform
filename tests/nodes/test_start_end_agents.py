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

    def test_chatbot_mode_derives_a_required_message_input(self):
        spec = _spec([
            {"id": "begin", "type": "StartAgent", "config": {"mode": "chatbot"}},
        ])
        assert spec.inputs["message"].type == "text"
        assert spec.inputs["message"].required is True

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


# ---------------------------------------------------------------------------
# Start — extended form field contract (format/widget/preset/conditional)
# ---------------------------------------------------------------------------

class TestStartFormatValidation:
    @pytest.mark.asyncio
    async def test_valid_email_passes(self):
        node = StartAgent("begin", {"fields": [
            {"name": "email", "label": "Email", "format": "email", "source": "inputs.email"},
        ]})
        output = await run(node, {"inputs": {"email": "john@example.com"}, "node_outputs": {}})
        assert output["data"]["email"] == "john@example.com"

    @pytest.mark.asyncio
    async def test_invalid_email_is_rejected_with_a_friendly_message(self):
        node = StartAgent("begin", {"fields": [
            {"name": "email", "label": "Business Email", "format": "email", "source": "inputs.email"},
        ]})
        with pytest.raises(ValueError, match="valid email address for Business Email"):
            await run(node, {"inputs": {"email": "not-an-email"}, "node_outputs": {}})

    @pytest.mark.asyncio
    async def test_invalid_url_is_rejected(self):
        node = StartAgent("begin", {"fields": [
            {"name": "site", "label": "Website", "format": "url", "source": "inputs.site"},
        ]})
        with pytest.raises(ValueError, match="valid website address"):
            await run(node, {"inputs": {"site": "not a url"}, "node_outputs": {}})

    @pytest.mark.asyncio
    async def test_permissive_international_phone_is_accepted(self):
        node = StartAgent("begin", {"fields": [
            {"name": "phone", "label": "Phone", "format": "phone", "required": False, "source": "inputs.phone"},
        ]})
        output = await run(node, {"inputs": {"phone": "+31 6 1234 5678"}, "node_outputs": {}})
        assert output["data"]["phone"] == "+31 6 1234 5678"


class TestStartLengthAndPattern:
    @pytest.mark.asyncio
    async def test_min_length_rejects_a_too_short_value(self):
        node = StartAgent("begin", {"fields": [
            {"name": "company", "label": "Company Name", "min_length": 3, "source": "inputs.company"},
        ]})
        with pytest.raises(ValueError, match="at least 3 characters"):
            await run(node, {"inputs": {"company": "AB"}, "node_outputs": {}})

    @pytest.mark.asyncio
    async def test_max_length_rejects_a_too_long_value(self):
        node = StartAgent("begin", {"fields": [
            {"name": "reference", "label": "Reference", "max_length": 5, "source": "inputs.reference"},
        ]})
        with pytest.raises(ValueError, match="at most 5 characters"):
            await run(node, {"inputs": {"reference": "TOO-LONG-REF"}, "node_outputs": {}})

    @pytest.mark.asyncio
    async def test_pattern_rejects_a_non_matching_value(self):
        node = StartAgent("begin", {"fields": [
            {"name": "order", "label": "Order Number", "pattern": r"^SO-\d+$", "source": "inputs.order"},
        ]})
        with pytest.raises(ValueError, match="not in the expected format"):
            await run(node, {"inputs": {"order": "12345"}, "node_outputs": {}})
        ok = await run(node, {"inputs": {"order": "SO-45882"}, "node_outputs": {}})
        assert ok["data"]["order"] == "SO-45882"


class TestStartPercentage:
    @pytest.mark.asyncio
    async def test_default_range_is_zero_to_a_hundred(self):
        node = StartAgent("begin", {"fields": [
            {"name": "discount", "label": "Discount", "type": "number", "format": "percentage", "source": "inputs.discount"},
        ]})
        with pytest.raises(ValueError, match="between 0 and 100"):
            await run(node, {"inputs": {"discount": 150}, "node_outputs": {}})
        ok = await run(node, {"inputs": {"discount": 8.5}, "node_outputs": {}})
        assert ok["data"]["discount"] == 8.5

    @pytest.mark.asyncio
    async def test_configured_range_overrides_default(self):
        node = StartAgent("begin", {"fields": [
            {
                "name": "priority", "label": "Priority", "type": "number", "format": "percentage",
                "minimum": 1, "maximum": 10, "source": "inputs.priority",
            },
        ]})
        with pytest.raises(ValueError, match="between 1 and 10"):
            await run(node, {"inputs": {"priority": 50}, "node_outputs": {}})


class TestStartCompoundPresets:
    def _object_field(self, name, label, preset, children, units=None):
        return {
            "name": name, "label": label, "type": "object", "preset": preset,
            "source": f"inputs.{name}", "units": units,
            "fields": children,
        }

    @pytest.mark.asyncio
    async def test_currency_resolves_as_amount_and_currency(self):
        node = StartAgent("begin", {"fields": [self._object_field(
            "budget", "Estimated Budget", "currency",
            [
                {"name": "amount", "type": "number"},
                {"name": "currency", "type": "enum", "enum_values": ["EUR", "USD"]},
            ],
            units=["EUR", "USD"],
        )]})
        output = await run(node, {
            "inputs": {"budget": {"amount": 25000, "currency": "EUR"}}, "node_outputs": {},
        })
        assert output["data"]["budget"] == {"amount": 25000, "currency": "EUR"}

    @pytest.mark.asyncio
    async def test_currency_outside_the_allowed_list_is_rejected(self):
        node = StartAgent("begin", {"fields": [self._object_field(
            "budget", "Estimated Budget", "currency",
            [
                {"name": "amount", "type": "number"},
                {"name": "currency", "type": "string"},
            ],
            units=["EUR", "USD"],
        )]})
        with pytest.raises(ValueError, match="not an allowed currency"):
            await run(node, {
                "inputs": {"budget": {"amount": 100, "currency": "GBP"}}, "node_outputs": {},
            })

    @pytest.mark.asyncio
    async def test_number_unit_resolves_value_and_unit(self):
        node = StartAgent("begin", {"fields": [self._object_field(
            "flow_rate", "Flow Rate", "number_unit",
            [
                {"name": "value", "type": "number"},
                {"name": "unit", "type": "enum", "enum_values": ["m3/h", "l/min"]},
            ],
            units=["m3/h", "l/min"],
        )]})
        output = await run(node, {
            "inputs": {"flow_rate": {"value": 120, "unit": "m3/h"}}, "node_outputs": {},
        })
        assert output["data"]["flow_rate"] == {"value": 120, "unit": "m3/h"}

    @pytest.mark.asyncio
    async def test_date_range_requires_end_on_or_after_start(self):
        node = StartAgent("begin", {"fields": [self._object_field(
            "period", "Required Period", "date_range",
            [
                {"name": "start", "type": "date"},
                {"name": "end", "type": "date"},
            ],
        )]})
        with pytest.raises(ValueError, match="end date must be on or after"):
            await run(node, {
                "inputs": {"period": {"start": "2026-09-15", "end": "2026-09-01"}}, "node_outputs": {},
            })
        ok = await run(node, {
            "inputs": {"period": {"start": "2026-09-01", "end": "2026-09-15"}}, "node_outputs": {},
        })
        assert ok["data"]["period"] == {"start": "2026-09-01", "end": "2026-09-15"}

    @pytest.mark.asyncio
    async def test_duration_resolves_value_and_unit(self):
        node = StartAgent("begin", {"fields": [self._object_field(
            "downtime", "Estimated Downtime", "duration",
            [
                {"name": "value", "type": "number"},
                {"name": "unit", "type": "enum", "enum_values": ["hours", "days"]},
            ],
            units=["hours", "days"],
        )]})
        output = await run(node, {
            "inputs": {"downtime": {"value": 4, "unit": "hours"}}, "node_outputs": {},
        })
        assert output["data"]["downtime"] == {"value": 4, "unit": "hours"}

    @pytest.mark.asyncio
    async def test_address_resolves_as_one_structured_value(self):
        node = StartAgent("begin", {"fields": [self._object_field(
            "address", "Address", "address",
            [
                {"name": "street", "type": "string"},
                {"name": "house_number", "type": "string"},
                {"name": "postal_code", "type": "string"},
                {"name": "city", "type": "string"},
                {"name": "country", "type": "string"},
            ],
        )]})
        value = {
            "street": "Main St", "house_number": "12", "postal_code": "1234AB",
            "city": "Utrecht", "country": "NL",
        }
        output = await run(node, {"inputs": {"address": value}, "node_outputs": {}})
        assert output["data"]["address"] == value


class TestStartRepeatingGroup:
    @pytest.mark.asyncio
    async def test_a_list_of_objects_resolves_as_typed_rows(self):
        node = StartAgent("begin", {"fields": [{
            "name": "products", "label": "Products Requested", "type": "list", "item_type": "object",
            "display": "table", "source": "inputs.products",
            "fields": [
                {"name": "product", "type": "string"},
                {"name": "quantity", "type": "integer"},
                {"name": "required_date", "type": "date"},
            ],
        }]})
        rows = [
            {"product": "Pump A", "quantity": 5, "required_date": "2026-08-30"},
            {"product": "Pump B", "quantity": 2, "required_date": "2026-09-05"},
        ]
        output = await run(node, {"inputs": {"products": rows}, "node_outputs": {}})
        assert output["data"]["products"] == rows


class TestStartInfoAndReadonlyFields:
    @pytest.mark.asyncio
    async def test_an_info_field_contributes_nothing_to_data(self):
        node = StartAgent("begin", {"fields": [
            {"name": "service_info", "label": "Service Information", "kind": "info",
             "description": "Please provide the serial number.", "required": False},
            {"name": "serial", "label": "Serial Number", "source": "inputs.serial"},
        ]})
        output = await run(node, {"inputs": {"serial": "SN-1"}, "node_outputs": {}})
        assert "service_info" not in output["data"]
        assert output["data"]["serial"] == "SN-1"

    def test_an_info_field_is_authorised_without_needing_a_value(self):
        fields = StartAgent.preflight_output_fields({
            "mode": "input_form",
            "fields": [{"name": "info_block", "kind": "info", "required": False}],
        })
        assert "data.info_block" not in fields

    @pytest.mark.asyncio
    async def test_a_readonly_field_resolves_like_a_normal_field(self):
        node = StartAgent("begin", {"fields": [
            {"name": "reference", "label": "Reference", "kind": "readonly", "source": "inputs.reference"},
        ]})
        output = await run(node, {"inputs": {"reference": "INQ-2026-00482"}, "node_outputs": {}})
        assert output["data"]["reference"] == "INQ-2026-00482"


class TestStartConditionalFields:
    @pytest.mark.asyncio
    async def test_a_hidden_field_is_dropped_and_never_required(self):
        node = StartAgent("begin", {"fields": [
            {"name": "inquiry_type", "label": "Inquiry Type", "type": "enum",
             "enum_values": ["technical", "existing_order"], "source": "inputs.inquiry_type"},
            {
                "name": "order_number", "label": "Order Number", "required": True,
                "source": "inputs.order_number",
                "visible_when": {"operator": "and", "conditions": [
                    {"field": "inquiry_type", "operator": "equals", "value": "existing_order"},
                ]},
            },
        ]})
        output = await run(node, {
            "inputs": {"inquiry_type": "technical", "order_number": "SO-1"}, "node_outputs": {},
        })
        assert "order_number" not in output["data"]
        assert "order_number" not in output["missing"]

    @pytest.mark.asyncio
    async def test_a_matching_condition_keeps_the_field_visible(self):
        node = StartAgent("begin", {"fields": [
            {"name": "inquiry_type", "label": "Inquiry Type", "type": "enum",
             "enum_values": ["technical", "existing_order"], "source": "inputs.inquiry_type"},
            {
                "name": "order_number", "label": "Order Number", "required": False,
                "source": "inputs.order_number",
                "visible_when": {"operator": "and", "conditions": [
                    {"field": "inquiry_type", "operator": "equals", "value": "existing_order"},
                ]},
            },
        ]})
        output = await run(node, {
            "inputs": {"inquiry_type": "existing_order", "order_number": "SO-1"}, "node_outputs": {},
        })
        assert output["data"]["order_number"] == "SO-1"

    @pytest.mark.asyncio
    async def test_required_when_adds_a_missing_field_only_if_the_condition_holds(self):
        node = StartAgent("begin", {"fields": [
            {"name": "inquiry_type", "label": "Inquiry Type", "type": "enum",
             "enum_values": ["technical", "existing_order"], "source": "inputs.inquiry_type"},
            {
                "name": "order_number", "label": "Order Number", "required": False,
                "source": "inputs.order_number",
                "required_when": {"operator": "and", "conditions": [
                    {"field": "inquiry_type", "operator": "equals", "value": "existing_order"},
                ]},
            },
        ]})
        blocked = await run(node, {"inputs": {"inquiry_type": "existing_order"}, "node_outputs": {}})
        assert "order_number" in blocked["missing"]

        fine = await run(node, {"inputs": {"inquiry_type": "technical"}, "node_outputs": {}})
        assert "order_number" not in fine["missing"]


class TestStartCustomerInquiryReferenceForm:
    """End-to-end capstone: the §32 Customer Inquiry form built from the full
    catalog together (radio + multi-select + conditional fields per request
    type + a repeating group), run through StartAgent.run() exactly as a real
    submission would arrive. Stands in for the plan's manual browser
    verification pass, which this session had no way to actually drive."""

    def _fields(self):
        return [
            {"name": "company_name", "label": "Company Name", "required": True,
             "source": "inputs.company_name"},
            {"name": "contact_name", "label": "Contact Name", "required": True,
             "source": "inputs.contact_name"},
            {"name": "email", "label": "Email", "format": "email", "required": True,
             "source": "inputs.email"},
            {"name": "phone", "label": "Phone", "format": "phone", "required": False,
             "source": "inputs.phone"},
            {"name": "request_types", "label": "Request Types", "type": "list", "item_type": "enum",
             "item_enum_values": ["existing_order", "service", "rfq", "complaint"],
             "widget": "multi_select", "required": True, "source": "inputs.request_types"},
            {
                "name": "order_number", "label": "Order Number", "required": False,
                "source": "inputs.order_number",
                "visible_when": {"operator": "and", "conditions": [
                    {"field": "request_types", "operator": "contains", "value": "existing_order"},
                ]},
                "required_when": {"operator": "and", "conditions": [
                    {"field": "request_types", "operator": "contains", "value": "existing_order"},
                ]},
            },
            {
                "name": "service_product", "label": "Product", "required": False,
                "source": "inputs.service_product",
                "visible_when": {"operator": "and", "conditions": [
                    {"field": "request_types", "operator": "contains", "value": "service"},
                ]},
            },
            {
                "name": "service_stopped", "label": "Has it stopped working?", "type": "boolean",
                "required": False, "source": "inputs.service_stopped",
                "visible_when": {"operator": "and", "conditions": [
                    {"field": "request_types", "operator": "contains", "value": "service"},
                ]},
            },
            {
                "name": "rfq_line_items", "label": "Requested Products", "type": "list", "item_type": "object",
                "display": "table", "required": False, "source": "inputs.rfq_line_items",
                "fields": [
                    {"name": "product", "type": "string"},
                    {"name": "quantity", "type": "integer"},
                ],
                "visible_when": {"operator": "and", "conditions": [
                    {"field": "request_types", "operator": "contains", "value": "rfq"},
                ]},
            },
            {
                "name": "complaint_details", "label": "Complaint Details", "type": "text", "required": False,
                "source": "inputs.complaint_details",
                "visible_when": {"operator": "and", "conditions": [
                    {"field": "request_types", "operator": "contains", "value": "complaint"},
                ]},
                "required_when": {"operator": "and", "conditions": [
                    {"field": "request_types", "operator": "contains", "value": "complaint"},
                ]},
            },
        ]

    @pytest.mark.asyncio
    async def test_an_rfq_submission_keeps_only_rfq_fields_and_types_the_line_items(self):
        node = StartAgent("begin", {"fields": self._fields()})
        line_items = [{"product": "Pump A", "quantity": 3}, {"product": "Pump B", "quantity": 1}]
        output = await run(node, {
            "inputs": {
                "company_name": "Acme BV", "contact_name": "Jan de Vries",
                "email": "jan@acme.example", "phone": "+31 6 1234 5678",
                "request_types": ["rfq"], "rfq_line_items": line_items,
                # Leftover values from a request type the customer un-toggled —
                # must be dropped, not validated, not sent through.
                "order_number": "should-not-appear", "complaint_details": "should-not-appear",
            },
            "node_outputs": {},
        })
        assert output["data"]["rfq_line_items"] == line_items
        assert "order_number" not in output["data"]
        assert "complaint_details" not in output["data"]
        assert "order_number" not in output["missing"]
        assert output["missing"] == []

    @pytest.mark.asyncio
    async def test_a_complaint_submission_requires_complaint_details(self):
        node = StartAgent("begin", {"fields": self._fields()})
        blocked = await run(node, {
            "inputs": {
                "company_name": "Acme BV", "contact_name": "Jan de Vries",
                "email": "jan@acme.example", "request_types": ["complaint"],
            },
            "node_outputs": {},
        })
        assert "complaint_details" in blocked["missing"]

        ok = await run(node, {
            "inputs": {
                "company_name": "Acme BV", "contact_name": "Jan de Vries",
                "email": "jan@acme.example", "request_types": ["complaint"],
                "complaint_details": "The unit is leaking at the flange.",
            },
            "node_outputs": {},
        })
        assert ok["data"]["complaint_details"] == "The unit is leaking at the flange."
        assert "complaint_details" not in ok["missing"]

    @pytest.mark.asyncio
    async def test_an_invalid_email_is_rejected_with_a_friendly_message(self):
        node = StartAgent("begin", {"fields": self._fields()})
        with pytest.raises(ValueError, match="valid email"):
            await run(node, {
                "inputs": {
                    "company_name": "Acme BV", "contact_name": "Jan de Vries",
                    "email": "not-an-email", "request_types": ["rfq"],
                },
                "node_outputs": {},
            })

    @pytest.mark.asyncio
    async def test_existing_order_and_service_toggled_together_shows_both_field_sets(self):
        node = StartAgent("begin", {"fields": self._fields()})
        output = await run(node, {
            "inputs": {
                "company_name": "Acme BV", "contact_name": "Jan de Vries",
                "email": "jan@acme.example",
                "request_types": ["existing_order", "service"],
                "order_number": "SO-2026-042", "service_product": "Model X Pump",
                "service_stopped": True,
            },
            "node_outputs": {},
        })
        assert output["data"]["order_number"] == "SO-2026-042"
        assert output["data"]["service_product"] == "Model X Pump"
        assert output["data"]["service_stopped"] is True
        assert "rfq_line_items" not in output["data"]
        assert "complaint_details" not in output["data"]


# ---------------------------------------------------------------------------
# Preflight — Start field-list validation
# ---------------------------------------------------------------------------

_START_FIELDS_YAML = """
name: test
nodes:
  - id: begin
    type: StartAgent
    config:
      fields:
{fields}
  - id: finish
    type: EndAgent
    config: {{}}
edges:
  - from: begin
    to: finish
entry: begin
exit: finish
"""


def test_duplicate_field_key_is_flagged():
    yaml_text = _START_FIELDS_YAML.format(fields=(
        "        - name: question\n          label: Question\n"
        "        - name: question\n          label: Question Again\n"
    ))
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "START_DUPLICATE_FIELD_KEY" in codes


def test_duplicate_option_value_is_flagged():
    yaml_text = _START_FIELDS_YAML.format(fields=(
        "        - name: request_type\n          label: Request Type\n          type: enum\n"
        "          enum_values: [rfq, rfq, service]\n"
    ))
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "START_DUPLICATE_OPTION_VALUE" in codes


def test_condition_referencing_an_unknown_field_is_flagged():
    yaml_text = _START_FIELDS_YAML.format(fields=(
        "        - name: order_number\n          label: Order Number\n"
        "          visible_when:\n            operator: and\n            conditions:\n"
        "              - field: nonexistent_field\n                operator: equals\n                value: x\n"
    ))
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "START_UNKNOWN_CONDITION_FIELD_REFERENCE" in codes


def test_condition_referencing_a_later_field_is_flagged():
    yaml_text = _START_FIELDS_YAML.format(fields=(
        "        - name: order_number\n          label: Order Number\n"
        "          visible_when:\n            operator: and\n            conditions:\n"
        "              - field: inquiry_type\n                operator: equals\n                value: existing_order\n"
        "        - name: inquiry_type\n          label: Inquiry Type\n          type: enum\n"
        "          enum_values: [existing_order]\n"
    ))
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "START_UNKNOWN_CONDITION_FIELD_REFERENCE" in codes


def test_a_condition_referencing_an_earlier_field_is_not_flagged():
    yaml_text = _START_FIELDS_YAML.format(fields=(
        "        - name: inquiry_type\n          label: Inquiry Type\n          type: enum\n"
        "          enum_values: [existing_order, technical]\n"
        "        - name: order_number\n          label: Order Number\n"
        "          visible_when:\n            operator: and\n            conditions:\n"
        "              - field: inquiry_type\n                operator: equals\n                value: existing_order\n"
    ))
    report = preflight_workflow_yaml(yaml_text)
    codes = {issue.code for issue in report.issues}
    assert "START_UNKNOWN_CONDITION_FIELD_REFERENCE" not in codes
