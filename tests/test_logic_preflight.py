"""Zero-token validation of visually authored business logic.

Each test builds a workflow with one specific authoring mistake and asserts the
exact preflight code an author would see. These are the mistakes a visual editor
makes easy to create and hard to notice — a threshold that can never be met, a
field name that doesn't exist, a branch with nowhere to go — and catching them
before a run is the difference between a demo that explains itself and a demo
that fails halfway through with a KeyError.

The other half of the contract matters just as much: a correct workflow must
produce *no* issues, or authors learn to ignore the panel.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from app.runtime.preflight import preflight_workflow_yaml

TRIAGE_PATH = Path("workflows/multilingual_customer_request_triage.yaml")
CRM_PATH = Path("workflows/crm_aware_customer_triage.yaml")


@pytest.fixture(scope="module")
def triage() -> dict:
    return yaml.safe_load(TRIAGE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def crm() -> dict:
    return yaml.safe_load(CRM_PATH.read_text(encoding="utf-8"))


def node(spec: dict, node_id: str) -> dict:
    return next(item for item in spec["nodes"] if item["id"] == node_id)


def branch_edge(spec: dict, source: str) -> dict:
    return next(
        edge
        for edge in spec["edges"]
        if edge.get("branches") and edge["from"] == source
    )


def rule(spec: dict, node_id: str, name_fragment: str) -> dict:
    return next(
        item
        for item in node(spec, node_id)["config"]["rules"]
        if name_fragment.lower() in item["name"].lower()
    )


def as_field_router(spec: dict) -> dict:
    """Rewrite the triage router into `field` mode.

    The shipped workflow uses `conditions` mode (one router, escalation case
    first). `field` mode — one branch per value of a classified field — is the
    other configuration authors reach for, and it is where the enum/branch checks
    have something to prove, so these tests convert to it explicitly rather than
    depending on which mode the example happens to use.
    """
    node(spec, "route_request")["config"] = {
        "mode": "field",
        "route_field": "outputs.understand_request.result.intent",
        "branches": {
            "quotation_request": "sales",
            "order_status": "sales",
            "technical_support": "technical_support",
            "spare_part_request": "spare_parts",
            "complaint": "customer_service",
            "general_inquiry": "human_review",
            "other": "human_review",
        },
        "fallback": "human_review",
    }
    return spec


def report_for(spec: dict):
    return preflight_workflow_yaml(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
        compile_graph=False,
    )


def codes(spec: dict) -> set[str]:
    return {issue.code for issue in report_for(spec).issues}


class TestTheExampleWorkflowIsClean:
    def test_the_shipped_triage_workflow_has_no_issues(self, triage):
        """If the reference workflow warns, every warning becomes background
        noise and the panel stops being read."""
        report = preflight_workflow_yaml(
            TRIAGE_PATH.read_text(encoding="utf-8"), compile_graph=True
        )
        assert report.valid, [issue.message for issue in report.errors]
        assert report.issues == []

    def test_the_business_logic_check_actually_ran(self, triage):
        report = report_for(triage)
        names = {check.name for check in report.checks}
        assert "business_logic" in names

    def test_validation_spends_no_tokens(self, triage):
        assert report_for(triage).tokens_spent == 0


class TestFieldReferences:
    def test_a_typo_in_an_extracted_field_is_caught(self, triage):
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "stopped production")["when"]["conditions"][0][
            "field"
        ] = "outputs.understand_request.result.production_stoped"
        report = report_for(spec)
        assert "UNKNOWN_FIELD_REFERENCE" in {issue.code for issue in report.issues}
        assert not report.valid

    def test_the_error_lists_what_the_step_does_produce(self, triage):
        """An author who mistyped a field needs the real names, not just a "no"."""
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "stopped production")["when"]["conditions"][0][
            "field"
        ] = "outputs.understand_request.result.nonsense"
        message = next(
            issue.message
            for issue in report_for(spec).issues
            if issue.code == "UNKNOWN_FIELD_REFERENCE"
        )
        assert "result.intent" in message

    def test_a_reference_to_a_nonexistent_step_is_caught(self, triage):
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "stopped production")["when"]["conditions"][0][
            "field"
        ] = "outputs.no_such_step.result.x"
        assert "UNKNOWN_FIELD_REFERENCE" in codes(spec)

    def test_reading_a_value_from_a_step_that_runs_later_is_caught(self, triage):
        """The graph-order mistake. `sales` runs after `automation_safety`, so on
        every real run the condition would see nothing."""
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "stopped production")["when"]["conditions"][0][
            "field"
        ] = "outputs.sales.data.company"
        assert "AI_OUTPUT_NOT_AVAILABLE_UPSTREAM" in codes(spec)

    def test_inputs_and_variables_references_are_left_alone(self, triage):
        """Those roots are validated by the existing input checks; re-reporting
        them here would double up on one mistake."""
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "stopped production")["when"]["conditions"][
            0
        ].update({"field": "inputs.message", "operator": "is_not_empty"})
        assert "UNKNOWN_FIELD_REFERENCE" not in codes(spec)


class TestOperatorTyping:
    def test_a_numeric_operator_on_a_list_is_rejected(self, triage):
        spec = copy.deepcopy(triage)
        target = rule(spec, "automation_safety", "spare part")["when"]["conditions"][1]
        target.update({"operator": "greater_or_equal", "value": 1})
        assert "RULE_TYPE_MISMATCH" in codes(spec)

    def test_a_string_compared_to_a_number_field_is_rejected(self, triage):
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "low confidence")["when"]["conditions"][0][
            "value"
        ] = "high"
        assert "RULE_TYPE_MISMATCH" in codes(spec)

    def test_a_valid_operator_produces_no_issue(self, triage):
        spec = copy.deepcopy(triage)
        target = rule(spec, "automation_safety", "spare part")["when"]["conditions"][1]
        target.update({"operator": "is_not_empty"})
        target.pop("value", None)
        assert "RULE_TYPE_MISMATCH" not in codes(spec)


class TestThresholds:
    def test_a_confidence_written_as_a_percentage_is_caught(self, triage):
        """The highest-value check here. `confidence >= 80` against a 0–1 field
        never fires, so nothing is ever escalated and every request looks
        confident — a silent failure that survives testing."""
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "low confidence")["when"]["conditions"][0][
            "value"
        ] = 80
        report = report_for(spec)
        issue = next(i for i in report.issues if i.code == "INVALID_THRESHOLD")
        assert "fraction, not a percentage" in issue.message
        assert not report.valid

    def test_a_negative_confidence_threshold_is_caught(self, triage):
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "low confidence")["when"]["conditions"][0][
            "value"
        ] = -1
        assert "INVALID_THRESHOLD" in codes(spec)

    def test_a_threshold_inside_the_range_is_accepted(self, triage):
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "low confidence")["when"]["conditions"][0][
            "value"
        ] = 0.65
        assert "INVALID_THRESHOLD" not in codes(spec)


class TestEnumValues:
    def test_a_rule_comparing_against_a_value_outside_the_enum_is_caught(self, triage):
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "complaint")["when"]["conditions"][0][
            "value"
        ] = "beschwerde"
        assert "INVALID_ENUM_VALUE" in codes(spec)

    def test_a_router_branching_on_an_impossible_value_is_caught(self, triage):
        spec = as_field_router(copy.deepcopy(triage))
        node(spec, "route_request")["config"]["branches"]["warranty_claim"] = (
            "customer_service"
        )
        assert "INVALID_ENUM_VALUE" in codes(spec)

    def test_a_literal_typed_runtime_field_is_checked_too(self, triage):
        """`status` is a Literal on the node's own output schema, not part of the
        author's visual schema — it is still a closed set."""
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "could not produce")["when"]["conditions"][0][
            "value"
        ] = "okay"
        assert "INVALID_ENUM_VALUE" in codes(spec)


class TestRouterBranches:
    def test_a_route_with_no_edge_is_caught(self, triage):
        spec = as_field_router(copy.deepcopy(triage))
        branch_edge(spec, "route_request")["branches"].pop("sales")
        report = report_for(spec)
        assert "ROUTER_BRANCH_WITHOUT_TARGET" in {i.code for i in report.issues}
        assert not report.valid

    def test_an_edge_branch_the_router_cannot_return_is_caught(self, triage):
        spec = as_field_router(copy.deepcopy(triage))
        branch_edge(spec, "route_request")["branches"]["legal"] = "human_review"
        assert "UNREACHABLE_BRANCH" in codes(spec)

    def test_a_router_without_a_fallback_is_warned_about(self, triage):
        spec = as_field_router(copy.deepcopy(triage))
        node(spec, "route_request")["config"].pop("fallback")
        assert "MISSING_DEFAULT_ROUTE" in codes(spec)

    def test_an_uncovered_enum_value_without_a_fallback_is_named(self, triage):
        spec = as_field_router(copy.deepcopy(triage))
        config = node(spec, "route_request")["config"]
        config.pop("fallback")
        config["branches"].pop("other")
        messages = [
            issue.message
            for issue in report_for(spec).issues
            if issue.code == "MISSING_DEFAULT_ROUTE"
        ]
        assert any("'other'" in message or "other" in message for message in messages)


class TestConditionalJoins:
    """The AND-join trap.

    The compiler makes every declared predecessor of a node a join requirement.
    Two routers both branching into one shared step therefore produce a node that
    waits for both and executes on neither — and the run reports **completed**
    with that step silently skipped. Found by simulating this very workflow with
    a two-router design; caught structurally now, because a simulation only
    reveals it on the input that happens to take that branch.
    """

    def _two_routers_into_one_gate(self, triage: dict) -> dict:
        spec = as_field_router(copy.deepcopy(triage))
        spec["nodes"].append(
            {
                "id": "automation_gate",
                "type": "RouterAgent",
                "config": {
                    "mode": "field",
                    "route_field": "outputs.automation_safety.decisions.human_review",
                    "branches": {"false": "route_request", "true": "human_review"},
                    "fallback": "human_review",
                },
            }
        )
        spec["edges"] = [
            edge
            for edge in spec["edges"]
            if not (edge["from"] == "automation_safety" and edge.get("to") == "route_request")
        ]
        spec["edges"].append({"from": "automation_safety", "to": "automation_gate"})
        spec["edges"].append(
            {
                "from": "automation_gate",
                "condition": "route",
                "branches": {
                    "route_request": "route_request",
                    "human_review": "human_review",
                },
            }
        )
        return spec

    def test_two_routers_into_one_step_is_blocked(self, triage):
        spec = self._two_routers_into_one_gate(triage)
        report = report_for(spec)
        assert "ROUTER_JOIN_UNREACHABLE" in {issue.code for issue in report.issues}
        assert not report.valid

    def test_the_message_explains_the_silent_skip(self, triage):
        """The failure mode is invisible at run time, so the message has to be the
        thing that makes it visible."""
        spec = self._two_routers_into_one_gate(triage)
        issue = next(
            i for i in report_for(spec).issues if i.code == "ROUTER_JOIN_UNREACHABLE"
        )
        assert "never execute" in issue.message
        assert "conditions mode" in (issue.suggestion or "")

    def test_one_router_with_a_case_per_outcome_is_accepted(self, triage):
        """The shape the shipped workflow uses, and what the suggestion points at."""
        assert "ROUTER_JOIN_UNREACHABLE" not in codes(copy.deepcopy(triage))

    def test_ordinary_parallel_fan_in_is_not_reported(self):
        """A genuine AND-join — two steps that always both run — is exactly what
        the compiler's join semantics are for, and must not be flagged."""
        spec = {
            "name": "fanin",
            "nodes": [
                {"id": "start", "type": "Literal", "config": {"value": 1}},
                {"id": "left", "type": "Echo", "config": {"template": "l"}},
                {"id": "right", "type": "Echo", "config": {"template": "r"}},
                {"id": "merge", "type": "Echo", "config": {"template": "m"}},
            ],
            "edges": [
                {"from": "start", "to": ["left", "right"]},
                {"from": "left", "to": "merge"},
                {"from": "right", "to": "merge"},
            ],
            "entry": "start",
            "exit": "merge",
        }
        report = preflight_workflow_yaml(yaml.safe_dump(spec), compile_graph=False)
        assert "ROUTER_JOIN_UNREACHABLE" not in {issue.code for issue in report.issues}


class TestSchemaContract:
    def test_an_enum_output_field_with_no_values_is_caught(self, triage):
        """Config validation catches this too; the point is that whichever check
        fires, the workflow is blocked rather than run."""
        spec = copy.deepcopy(triage)
        fields = node(spec, "understand_request")["config"]["output_fields"]
        next(item for item in fields if item["name"] == "intent")["enum_values"] = []
        assert not report_for(spec).valid


class TestExternalActions:
    def _with_email_send(self, triage: dict, *, reviewed: bool) -> dict:
        """Append an email send to the triage workflow, with or without a human
        gate guaranteed in front of it."""
        spec = copy.deepcopy(triage)
        spec["nodes"].append(
            {
                "id": "send_reply",
                "type": "EmailAgent",
                "config": {
                    "connection": "support_inbox",
                    "operation": "send",
                    "to": [{"email": "kunde@werke.de"}],
                    "subject": "Re: your request",
                    "body": "We are on it.",
                },
            }
        )
        source = "human_review" if reviewed else "technical_support"
        spec["edges"].append({"from": source, "to": "send_reply"})
        spec["exit"] = [
            item for item in spec["exit"] if item != source
        ] + ["send_reply"]
        return spec

    def test_a_send_with_no_human_review_in_front_is_warned_about(self, triage):
        """A warning, not an error: automating a reply is a legitimate choice.
        What must not happen is making it silently."""
        spec = self._with_email_send(triage, reviewed=False)
        report = report_for(spec)
        issue = next(
            i for i in report.issues if i.code == "EXTERNAL_ACTION_WITHOUT_REVIEW"
        )
        assert issue.severity.value == "warning"
        assert report.valid

    def test_a_send_behind_a_human_review_is_not_warned_about(self, triage):
        spec = self._with_email_send(triage, reviewed=True)
        assert "EXTERNAL_ACTION_WITHOUT_REVIEW" not in codes(spec)

    def test_creating_a_draft_needs_no_review_warning(self, triage):
        """A draft is the safe form of an outward action — nothing reaches the
        recipient until a person sends it."""
        spec = self._with_email_send(triage, reviewed=False)
        node(spec, "send_reply")["config"]["operation"] = "create_draft"
        assert "EXTERNAL_ACTION_WITHOUT_REVIEW" not in codes(spec)

    def test_the_email_service_is_a_required_service(self, triage):
        spec = self._with_email_send(triage, reviewed=True)
        assert "email" in report_for(spec).required_services


class TestMCPTools:
    """MCP steps, checked without contacting a server.

    Preflight is zero-token *and* zero-network: a Builder check must not depend
    on a CRM being reachable. So it validates what is decidable offline — the
    server is configured, the tool is permitted, a write is not scheduled to run
    unattended — and leaves "does this tool exist" to the discovery panel, which
    can reach the server.
    """

    def test_the_shipped_crm_workflow_has_no_issues(self, crm):
        report = preflight_workflow_yaml(
            CRM_PATH.read_text(encoding="utf-8"), compile_graph=True
        )
        assert report.valid, [issue.message for issue in report.errors]
        assert report.issues == []

    def test_the_crm_workflow_declares_the_mcp_service(self, crm):
        assert "mcp" in report_for(crm).required_services

    def test_an_unconfigured_server_is_caught(self, crm):
        spec = copy.deepcopy(crm)
        node(spec, "find_crm_account")["config"]["server_id"] = "salesforce"
        report = report_for(spec)
        assert "MCP_SERVER_NOT_CONFIGURED" in {i.code for i in report.issues}
        assert not report.valid

    def test_a_step_with_no_tool_selected_is_caught(self, crm):
        spec = copy.deepcopy(crm)
        node(spec, "find_crm_account")["config"]["tool"] = ""
        assert "MCP_TOOL_NOT_CONFIGURED" in codes(spec)

    def test_a_crm_write_with_no_human_review_is_warned_about(self, crm):
        """A warning, not an error: automating a CRM write is a legitimate
        choice. Making it silently is not."""
        spec = copy.deepcopy(crm)
        spec["nodes"].append(
            {
                "id": "log_activity",
                "type": "MCPToolAgent",
                "config": {
                    "server_id": "dynamics365",
                    "tool": "create_followup_activity",
                    "arguments": {
                        "account_id": "{{outputs.find_crm_account.first.account_id?}}",
                        "subject": "Follow up",
                    },
                },
            }
        )
        spec["edges"].append({"from": "technical_support", "to": "log_activity"})
        spec["exit"] = [
            item for item in spec["exit"] if item != "technical_support"
        ] + ["log_activity"]

        report = report_for(spec)
        issue = next(
            i for i in report.issues if i.code == "EXTERNAL_ACTION_WITHOUT_REVIEW"
        )
        assert issue.severity.value == "warning"
        assert "changes data" in issue.message
        assert report.valid

    def test_declaring_the_write_unattended_silences_the_warning(self, crm):
        """The author stated the decision, and it is visible on the canvas."""
        spec = copy.deepcopy(crm)
        spec["nodes"].append(
            {
                "id": "log_activity",
                "type": "MCPToolAgent",
                "config": {
                    "server_id": "dynamics365",
                    "tool": "create_followup_activity",
                    "arguments": {"account_id": "x", "subject": "Follow up"},
                    "allow_unattended_write": True,
                },
            }
        )
        spec["edges"].append({"from": "technical_support", "to": "log_activity"})
        spec["exit"] = [
            item for item in spec["exit"] if item != "technical_support"
        ] + ["log_activity"]
        assert "EXTERNAL_ACTION_WITHOUT_REVIEW" not in codes(spec)

    def test_a_read_needs_no_review(self, crm):
        assert "EXTERNAL_ACTION_WITHOUT_REVIEW" not in codes(copy.deepcopy(crm))


class TestRobustness:
    def test_a_workflow_using_only_specialized_nodes_is_unaffected(self):
        """The new checks must not start reporting on the 43 pre-existing node
        types, whose outputs are free-form dicts these checks cannot reason
        about."""
        spec = {
            "name": "legacy",
            "nodes": [
                {"id": "a", "type": "Literal", "config": {"value": 1}},
                {"id": "b", "type": "Echo", "config": {"template": "{{a.value}}"}},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "entry": "a",
            "exit": "b",
        }
        report = preflight_workflow_yaml(
            yaml.safe_dump(spec), compile_graph=False
        )
        assert report.valid
        assert report.issues == []

    def test_a_malformed_rule_is_reported_once_by_config_validation(self, triage):
        """Not twice: a broken condition should not produce both a config error
        and a vaguer logic error about the same line."""
        spec = copy.deepcopy(triage)
        rule(spec, "automation_safety", "stopped production")["when"]["conditions"][0][
            "operator"
        ] = "is_definitely"
        report = report_for(spec)
        assert not report.valid
        assert "LOGIC_CHECK_FAILED" not in {issue.code for issue in report.issues}
