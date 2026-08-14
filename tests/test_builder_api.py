"""The Builder's authoring API.

These endpoints are what make the visual editor specific rather than generic: the
operators offered, the fields shown, the result of testing one step, the trace of
a simulated run. Two invariants are asserted throughout, because breaking either
one would make the Builder lie to its user:

*   what /output-contract offers is exactly what preflight authorises, and the
    operators it lists are exactly the ones the rule engine implements;
*   nothing here mutates a saved workflow, and no external side effect can be
    triggered from the Test tab.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.runtime.rules import OPERATORS_BY_TYPE

TRIAGE_YAML = Path(
    "workflows/multilingual_customer_request_triage.yaml"
).read_text(encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def _unthrottled():
    """Exempt this module from the shared request-rate limit.

    The Builder API is chatty by design — an author hits /output-contract and
    /node-test on nearly every keystroke-sized edit — so this module alone makes
    more requests per minute than the platform's default budget allows. Left
    throttled, it does not just fail itself: it drains the per-minute allowance
    other test modules need for their own logins. The limiter has its own
    coverage in test_production_controls.py, so switching it off here removes
    cross-module interference without losing anything.
    """
    from app.config import settings

    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    yield
    settings.rate_limit_enabled = original


@pytest.fixture(scope="module")
def client(_unthrottled):
    with TestClient(app) as instance:
        instance.post("/auth/token", data={"username": "ayush", "password": "dev123"})
        yield instance


class TestOperatorCatalog:
    def test_it_serves_the_engine_s_own_table(self, client):
        """The editor must not carry its own copy of this: a hardcoded list is
        how an editor ends up offering an operator the runtime rejects."""
        body = client.get("/api/builder/operators").json()
        assert body["by_type"]["list"] == list(OPERATORS_BY_TYPE["list"])
        assert body["by_type"]["boolean"] == list(OPERATORS_BY_TYPE["boolean"])

    def test_arity_tells_the_editor_which_inputs_to_render(self, client):
        body = client.get("/api/builder/operators").json()
        assert body["arity"]["is_empty"] == "none"
        assert body["arity"]["equals"] == "one"
        assert body["arity"]["in"] == "many"

    def test_every_operator_has_a_human_label(self, client):
        body = client.get("/api/builder/operators").json()
        for operators in body["by_type"].values():
            for operator in operators:
                assert body["labels"].get(operator)


class TestOutputContract:
    def test_it_returns_typed_paths_for_a_visually_built_schema(self, client):
        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "automation_safety"},
        ).json()
        understand = next(
            item for item in body["nodes"] if item["node_id"] == "understand_request"
        )
        fields = {item["path"]: item for item in understand["fields"]}

        assert fields["result.request_types"]["type"] == "list"
        assert "technical_support" in fields["result.request_types"]["enum_values"]
        assert fields["result.product_model"]["type"] == "string"
        assert fields["confidence"]["type"] == "number"

    def test_each_field_carries_the_reference_the_editor_should_write(self, client):
        """So an author never types `{{outputs.understand_request.result.request_types}}`
        by hand — clicking the field is the mapping (§14)."""
        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "automation_safety"},
        ).json()
        understand = next(
            item for item in body["nodes"] if item["node_id"] == "understand_request"
        )
        request_types = next(
            item for item in understand["fields"] if item["path"] == "result.request_types"
        )
        assert request_types["reference"] == "{{outputs.understand_request.result.request_types}}"

    def test_it_flags_values_that_may_be_unavailable(self, client):
        """§15: a required field inside an optional object can still be null, and
        the mapping panel has to say so."""
        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "automation_safety"},
        ).json()
        understand = next(
            item for item in body["nodes"] if item["node_id"] == "understand_request"
        )
        email = next(
            item
            for item in understand["fields"]
            if item["path"] == "result.requestor.email"
        )
        assert email["may_be_unavailable"] is True

    def test_operators_are_attached_per_field(self, client):
        """§39: the editor offers `contains` for a list and `>=` for a number
        because the contract says so, not because the editor guessed."""
        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "automation_safety"},
        ).json()
        understand = next(
            item for item in body["nodes"] if item["node_id"] == "understand_request"
        )
        fields = {item["path"]: item for item in understand["fields"]}
        assert "contains" in fields["result.missing_information"]["operators"]
        assert "greater_or_equal" not in fields["result.missing_information"]["operators"]
        assert "greater_or_equal" in fields["confidence"]["operators"]

    def test_only_values_that_can_reach_the_node_are_offered(self, client):
        """A mapping picker offering a downstream step's output would let an
        author build a reference that can never resolve."""
        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "automation_safety"},
        ).json()
        offered = {item["node_id"] for item in body["nodes"]}
        assert "understand_request" in offered
        assert "sales" not in offered
        assert "automation_safety" not in offered

    def test_business_labels_come_back_with_the_technical_ids(self, client):
        """§17: the picker shows "Understand Customer Request", the reference
        uses the id."""
        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "automation_safety"},
        ).json()
        understand = next(
            item for item in body["nodes"] if item["node_id"] == "understand_request"
        )
        assert understand["label"] == "Understand Customer Request"
        assert understand["execution_kind"] == "ai"

    def test_workflow_inputs_and_variables_are_offered_too(self, client):
        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "understand_request"},
        ).json()
        assert {item["name"] for item in body["inputs"]} >= {"message", "subject"}

    def test_what_it_offers_is_what_preflight_authorises(self, client):
        """The invariant that makes the editor trustworthy. Every path offered
        here must be a path a template reference may legally use."""
        from app.nodes.ai_task import AITaskAgent
        from app.runtime.loader import load_workflow_from_string

        spec = load_workflow_from_string(TRIAGE_YAML)
        node = next(item for item in spec.nodes if item.id == "understand_request")
        authorised = AITaskAgent.preflight_output_fields(node.effective_config())

        body = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": TRIAGE_YAML, "node_id": "automation_safety"},
        ).json()
        understand = next(
            item for item in body["nodes"] if item["node_id"] == "understand_request"
        )
        for field in understand["fields"]:
            assert field["path"] in authorised, field["path"]

    def test_invalid_yaml_is_a_400_not_a_500(self, client):
        response = client.post(
            "/api/builder/output-contract",
            json={"workflow_yaml": "nodes: [oh dear"},
        )
        assert response.status_code == 400


class TestSchemaPreview:
    def test_it_compiles_rows_into_json_schema(self, client):
        body = client.post(
            "/api/builder/schema-preview",
            json={
                "output_fields": [
                    {
                        "name": "intent",
                        "type": "enum",
                        "enum_values": ["a", "b"],
                        "description": "What they want.",
                    },
                    {
                        "name": "customer",
                        "type": "object",
                        "required": False,
                        "fields": [{"name": "company", "type": "string"}],
                    },
                ]
            },
        ).json()
        assert body["json_schema"]["required"] == ["intent"]
        assert "customer.company" in {item["path"] for item in body["paths"]}
        assert "one of: a, b" in body["contract"]

    def test_an_invalid_row_is_reported_while_the_author_is_still_editing(self, client):
        """A 422 with the reason, rather than a schema that fails at run time."""
        response = client.post(
            "/api/builder/schema-preview",
            json={"output_fields": [{"name": "intent", "type": "enum"}]},
        )
        assert response.status_code == 422
        assert "allowed value" in response.json()["detail"]["message"]


class TestNodeTest:
    def test_a_deterministic_node_runs_against_sample_data(self, client):
        body = client.post(
            "/api/builder/node-test",
            json={
                "type_name": "DecisionAgent",
                "node_id": "safety",
                "config": {
                    "defaults": {"human_review": False},
                    "rules": [
                        {
                            "name": "Low confidence needs a person",
                            "when": {
                                "operator": "and",
                                "conditions": [
                                    {
                                        "field": "outputs.understand.confidence",
                                        "operator": "less_than",
                                        "value": 0.8,
                                    }
                                ],
                            },
                            "then": [{"field": "human_review", "value": True}],
                        }
                    ],
                },
                "upstream_outputs": {"understand": {"confidence": 0.64}},
            },
        ).json()
        assert body["status"] == "completed"
        assert body["output"]["decisions"]["human_review"] is True

    def test_the_result_explains_what_decided_it(self, client):
        """§47: a reviewer needs to know a rule decided this, not a model."""
        body = client.post(
            "/api/builder/node-test",
            json={
                "type_name": "RouterAgent",
                "node_id": "route",
                "config": {
                    "mode": "field",
                    "route_field": "outputs.understand.result.intent",
                    "branches": {"technical_support": "support"},
                    "fallback": "human_review",
                },
                "upstream_outputs": {
                    "understand": {"result": {"intent": "technical_support"}}
                },
            },
        ).json()
        assert body["explanation"]["decided_by"] == "Deterministic routing"
        assert body["explanation"]["route"] == "support"
        assert body["explanation"]["used_fallback"] is False

    def test_a_bad_config_is_reported_against_the_config(self, client):
        response = client.post(
            "/api/builder/node-test",
            json={
                "type_name": "AITaskAgent",
                "config": {"task": "extract", "input": "x"},
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["stage"] == "config"
        assert "output schema" in response.json()["detail"]["message"]

    def test_a_missing_sample_value_is_reported_against_the_mapping(self, client):
        """The most common Test-tab mistake: the config references an upstream
        value the sample data doesn't have. Say that, don't raise a KeyError."""
        response = client.post(
            "/api/builder/node-test",
            json={
                "type_name": "DataTransformAgent",
                "config": {
                    "operations": [
                        {
                            "target": "title",
                            "operation": "format",
                            "value": "{{outputs.missing.field}}",
                        }
                    ]
                },
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["stage"] == "mapping"
        assert "sample" in response.json()["detail"]["hint"]

    def test_a_runtime_failure_returns_the_error_rather_than_a_500(self, client):
        """An author iterating on config sees the failure in the panel; a 500
        would look like the platform broke."""
        body = client.post(
            "/api/builder/node-test",
            json={
                "type_name": "RouterAgent",
                "config": {
                    "mode": "field",
                    "route_field": "outputs.understand.result.intent",
                    "branches": {"technical_support": "support"},
                },
                "upstream_outputs": {"understand": {"result": {"intent": "unknown"}}},
            },
        ).json()
        assert body["status"] == "failed"
        assert "matches no branch" in body["error"]

    def test_an_email_send_cannot_be_triggered_from_the_test_tab(self, client):
        """A test is something an author runs repeatedly while tweaking wording.
        A send is not."""
        response = client.post(
            "/api/builder/node-test",
            json={
                "type_name": "EmailAgent",
                "config": {
                    "connection": "whatever",
                    "operation": "send",
                    "to": [{"email": "a@b.c"}],
                    "subject": "s",
                    "body": "b",
                },
            },
        )
        assert response.status_code == 400
        assert "cannot be run from the Test tab" in response.json()["detail"]

    def test_an_unknown_node_type_is_a_404(self, client):
        response = client.post(
            "/api/builder/node-test",
            json={"type_name": "NoSuchAgent", "config": {}},
        )
        assert response.status_code == 404

    def test_the_resolved_config_is_returned_for_inspection(self, client):
        """Showing what the step actually received after mapping is what turns
        "why did it do that" into a one-glance answer."""
        body = client.post(
            "/api/builder/node-test",
            json={
                "type_name": "DataTransformAgent",
                "config": {
                    "operations": [
                        {
                            "target": "title",
                            "operation": "format",
                            "value": "Subject: {{inputs.subject}}",
                        }
                    ]
                },
                "inputs": {"subject": "Pumpe ausgefallen"},
            },
        ).json()
        assert body["output"]["data"]["title"] == "Subject: Pumpe ausgefallen"
        assert "Pumpe ausgefallen" in str(body["resolved_config"])


class TestSimulation:
    """A simulation runs the real runtime. Stubbing the AI step keeps these
    tests hermetic while still exercising the compiler, the rule engine, the
    routers and the trace assembly end to end."""

    EXTRACTED = {
        "result": {
            "language": "de",
            "request_types": ["technical_support"],
            "request_summary": "The customer's pump has failed and production is down.",
            "requestor": {"name": None, "email": None, "phone": None},
            "organization": "Werke GmbH",
            "product": None,
            "product_model": "Dura 15",
            "serial_number": "82912",
            "quantity": None,
            "medium": None,
            "flow_rate": None,
            "production_stopped": True,
            "urgency": "critical",
            "requested_action": "Call us urgently.",
            "missing_information": [],
            "blocking_missing_information": [],
            "ambiguities": [],
            "suggested_actions": ["Create an urgent technical support case"],
            "confidence": 0.93,
            "reasoning": "Stated failure and stopped production.",
        },
        "text": "",
        "status": "ok",
        "error": None,
        "confidence": 0.93,
        "reasoning": "Stated failure and stopped production.",
        "detected_language": "de",
        "model_used": "stub",
        "attempts": 1,
    }

    def simulate(self, client, *, confidence: float, **overrides):
        extracted = {
            **self.EXTRACTED,
            "confidence": confidence,
            "result": {**self.EXTRACTED["result"], "confidence": confidence},
        }
        return client.post(
            "/api/builder/simulate",
            json={
                "workflow_yaml": TRIAGE_YAML,
                "inputs": {
                    "message": "Unsere Dura 15 Pumpe ist ausgefallen. Seriennummer 82912.",
                    "subject": "Pumpe ausgefallen",
                },
                "stub_outputs": {"understand_request": extracted},
                **overrides,
            },
        ).json()

    def test_a_confident_technical_request_reaches_technical_support(self, client):
        body = self.simulate(client, confidence=0.93)
        assert body["status"] == "completed"
        assert "technical_support" in body["path"]
        assert "human_review" not in body["path"]

    def test_lowering_the_confidence_reroutes_to_human_review(self, client):
        """§44's live demonstration: change one number, rerun, and the graph
        visibly goes somewhere else. Nothing about the workflow changed."""
        body = self.simulate(client, confidence=0.64)
        assert "human_review" in body["path"]
        assert "technical_support" not in body["path"]

    def test_the_escalation_reason_names_the_rule_that_fired(self, client):
        body = self.simulate(client, confidence=0.64)
        safety = next(
            step for step in body["steps"] if step["node_id"] == "automation_safety"
        )
        assert "Low confidence needs a person" in safety["explanation"]["matched_rules"]
        assert "0.80 threshold" in safety["output"]["decisions"]["escalation_reason"]

    def test_every_step_reports_what_kind_of_thing_decided_it(self, client):
        """§25/§47: AI inference, deterministic rule, external tool and human
        decision are distinguishable in the trace."""
        body = self.simulate(client, confidence=0.93)
        by_id = {step["node_id"]: step for step in body["steps"]}
        assert by_id["understand_request"]["explanation"]["decided_by"] == "AI inference"
        assert (
            by_id["automation_safety"]["explanation"]["decided_by"]
            == "Deterministic rules"
        )
        assert by_id["route_request"]["explanation"]["decided_by"] == "Deterministic routing"

    def test_the_router_step_explains_the_branch_it_took(self, client):
        body = self.simulate(client, confidence=0.93)
        router = next(
            step for step in body["steps"] if step["node_id"] == "route_request"
        )
        assert router["explanation"]["route"] == "technical_support"
        assert "technical_support" in router["explanation"]["summary"][0]

    def test_a_stubbed_step_is_labelled_as_stubbed(self, client):
        """So nobody mistakes a frozen value for a real model call."""
        body = self.simulate(client, confidence=0.93)
        understand = next(
            step for step in body["steps"] if step["node_id"] == "understand_request"
        )
        assert understand["stubbed"] is True
        assert body["stubbed"] == ["understand_request"]

    def test_business_labels_appear_on_every_step(self, client):
        body = self.simulate(client, confidence=0.93)
        labels = {step["node_id"]: step["label"] for step in body["steps"]}
        assert labels["understand_request"] == "Understand Customer Request"
        assert labels["automation_safety"] == "Check Automation Safety"

    def test_it_can_stop_at_a_chosen_step(self, client):
        """§22: the smallest valid upstream slice, without touching the saved
        workflow."""
        body = self.simulate(
            client, confidence=0.93, until_node="automation_safety"
        )
        assert body["status"] == "completed"
        assert "route_request" not in body["path"]
        assert "automation_safety" in body["path"]

    def test_a_deterministic_rule_step_costs_nothing_and_repeats_exactly(self, client):
        first = self.simulate(client, confidence=0.93)
        second = self.simulate(client, confidence=0.93)
        pick = lambda body: next(  # noqa: E731
            step["output"]["decisions"]
            for step in body["steps"]
            if step["node_id"] == "automation_safety"
        )
        assert pick(first) == pick(second)

    def test_stubbing_an_unknown_step_is_rejected(self, client):
        response = client.post(
            "/api/builder/simulate",
            json={
                "workflow_yaml": TRIAGE_YAML,
                "inputs": {"message": "x"},
                "stub_outputs": {"no_such_step": {}},
            },
        )
        assert response.status_code == 422

    def test_a_workflow_with_preflight_errors_is_not_run(self, client):
        """Nothing executes, and the report says why — the same zero-token
        report the Checks panel shows."""
        broken = TRIAGE_YAML.replace(
            "outputs.understand_request.result.production_stopped",
            "outputs.understand_request.result.production_stoped",
        )
        response = client.post(
            "/api/builder/simulate",
            json={"workflow_yaml": broken, "inputs": {"message": "x"}},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "Nothing was run" in detail["message"]
        assert detail["preflight"]["valid"] is False

    def test_a_simulation_does_not_create_a_run_record(self, client):
        """Simulations are for understanding a workflow, not auditing one. A
        simulation appearing in Run History would corrupt the run statistics."""
        before = client.get("/api/runs/mine").json()
        self.simulate(client, confidence=0.93)
        after = client.get("/api/runs/mine").json()
        assert before == after


class TestEmailConnections:
    def test_it_reports_configured_mailboxes_without_credentials(self, client):
        body = client.get("/api/builder/email/connections").json()
        assert "connections" in body
        assert "configured" in body
        assert "token" not in str(body).lower()

    def test_no_configured_mailbox_is_not_an_error(self, client):
        """An unconfigured deployment still opens the Builder; preflight is where
        a workflow needing a mailbox is blocked."""
        response = client.get("/api/builder/email/connections")
        assert response.status_code == 200


class TestMCPDiscovery:
    """The Builder's window onto connected business systems.

    Exercised against the real Dynamics MCP subprocess in mock mode, so these
    assertions cover the actual protocol round trip — discovery, schemas,
    classification and a live tool call — not a stubbed approximation.
    """

    def test_configured_servers_are_listed(self, client):
        body = client.get("/api/builder/mcp/servers").json()
        assert body["configured"] is True
        assert "dynamics365" in {server["id"] for server in body["servers"]}

    def test_a_mock_connection_says_so(self, client):
        """A green "Connected" badge over fixtures is how a demo becomes a lie."""
        body = client.get("/api/builder/mcp/servers").json()
        dynamics = next(s for s in body["servers"] if s["id"] == "dynamics365")
        assert dynamics["is_mock"] is True
        assert dynamics["environment_label"] == "Demo fixtures"

    def test_the_server_list_never_contains_a_credential(self, client):
        body = client.get("/api/builder/mcp/servers").json()
        dynamics = next(s for s in body["servers"] if s["id"] == "dynamics365")
        # It names which variables are expected and whether each is set…
        assert {item["variable"] for item in dynamics["credentials"]} >= {
            "DYNAMICS_CLIENT_ID",
            "DYNAMICS_CLIENT_SECRET",
        }
        # …and carries no value for any of them.
        assert all("value" not in item for item in dynamics["credentials"])

    def test_tools_are_discovered_from_the_running_server(self, client):
        body = client.get("/api/builder/mcp/servers/dynamics365/tools").json()
        names = {tool["name"] for tool in body["tools"]}
        assert "find_account" in names
        assert "get_open_opportunities" in names
        assert body["count"] == len(body["tools"])

    def test_each_tool_is_classified_read_or_write(self, client):
        body = client.get("/api/builder/mcp/servers/dynamics365/tools").json()
        tools = {tool["name"]: tool for tool in body["tools"]}
        assert tools["find_account"]["operation"] == "read"
        assert tools["create_lead"]["operation"] == "write"
        assert tools["create_lead"]["requires_approval"] is True

    def test_each_tool_carries_the_schema_the_form_is_generated_from(self, client):
        """§6: the Builder renders the form from this, so a tool added to the
        server needs no frontend change."""
        body = client.get("/api/builder/mcp/servers/dynamics365/tools").json()
        tool = next(t for t in body["tools"] if t["name"] == "find_account")
        properties = tool["input_schema"]["properties"]
        assert "company_name" in properties
        assert tool["input_schema"]["required"] == ["company_name"]
        assert properties["company_name"]["description"]

    def test_each_tool_carries_mappable_output_paths(self, client):
        body = client.get("/api/builder/mcp/servers/dynamics365/tools").json()
        tool = next(t for t in body["tools"] if t["name"] == "find_account")
        paths = {field["path"] for field in tool["output_fields"]}
        assert "accounts.items.account_id" in paths

    def test_each_tool_explains_itself_in_business_language(self, client):
        body = client.get("/api/builder/mcp/servers/dynamics365/tools").json()
        tool = next(t for t in body["tools"] if t["name"] == "get_open_opportunities")
        assert tool["system"] == "Microsoft Dynamics 365"
        assert tool["typical_uses"]

    def test_an_unknown_server_is_a_404(self, client):
        assert client.get("/api/builder/mcp/servers/nope/tools").status_code == 404
        assert client.get("/api/builder/mcp/servers/nope/health").status_code == 404

    def test_health_reports_the_live_connection(self, client):
        body = client.get("/api/builder/mcp/servers/dynamics365/health").json()
        assert body["healthy"] is True
        assert body["tool_count"] > 0


class TestMCPToolTest:
    def test_a_read_tool_can_be_run_from_the_builder(self, client):
        """§21: type a company name, press Test, see the CRM answer."""
        body = client.post(
            "/api/builder/mcp/test-tool",
            json={
                "server_id": "dynamics365",
                "tool": "find_account",
                "arguments": {"company_name": "ABC Chemicals"},
            },
        ).json()
        assert body["status"] == "completed"
        assert body["is_structured"] is True
        assert body["mode"] == "mock"
        assert body["data"]["count"] >= 1

    def test_a_write_tool_cannot_be_run_from_the_builder(self, client):
        """A test is something an author runs repeatedly while adjusting
        inputs. Twenty leads in a real CRM is a real mess."""
        response = client.post(
            "/api/builder/mcp/test-tool",
            json={
                "server_id": "dynamics365",
                "tool": "create_lead",
                "arguments": {"subject": "test"},
            },
        )
        assert response.status_code == 400
        assert "cannot be run from the Test panel" in response.json()["detail"]

    def test_a_tool_error_is_returned_structurally_not_as_a_500(self, client):
        body = client.post(
            "/api/builder/mcp/test-tool",
            json={
                "server_id": "dynamics365",
                "tool": "get_open_opportunities",
                "arguments": {"account_id": "not-a-guid"},
            },
        ).json()
        assert body["status"] == "failed"
        assert body["error"]["code"] == "CRM_INVALID_ARGUMENTS"
        assert body["error"]["suggested_action"]

    def test_an_unknown_tool_is_a_404(self, client):
        response = client.post(
            "/api/builder/mcp/test-tool",
            json={"server_id": "dynamics365", "tool": "no_such_tool"},
        )
        assert response.status_code == 404


class TestAuthentication:
    def test_the_builder_api_is_not_anonymous(self, client):
        client.cookies.clear()
        response = client.get("/api/builder/operators", headers={"Authorization": ""})
        assert response.status_code == 401
