"""The MCP tool policy gate.

This is the file that decides whether a language model can cause a write to a
customer's CRM. The tests are written accordingly: each one states a way the
gate could be talked around, and asserts that it cannot be.

The load-bearing property is the classification precedence — deployment policy
beats the server's own annotations, which never lower a classification, which
beats name heuristics, which default to "treat as a write".
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.mcp.policy import (
    MCPApprovalRequired,
    MCPPolicyError,
    classify_by_name,
    classify_tool,
    evaluate,
    is_write,
)
from app.mcp.registry import (
    MCPServerConnection,
    MCPServerRegistry,
    MCPToolPolicy,
    load_servers,
)


class Annotations:
    """Stands in for the SDK's ToolAnnotations."""

    def __init__(self, *, read_only=None, destructive=None):
        self.readOnlyHint = read_only
        self.destructiveHint = destructive


def connection(**overrides) -> MCPServerConnection:
    base = {
        "id": "crm",
        "display_name": "Test CRM",
        "command": "python",
        "args": ["-m", "whatever"],
    }
    base.update(overrides)
    return MCPServerConnection(**base)


class TestNameClassification:
    @pytest.mark.parametrize(
        "name",
        ["get_account", "fetch-accounts", "findContact", "list_orders", "search_products"],
    )
    def test_read_verbs_are_reads(self, name):
        assert classify_by_name(name) == "read"

    @pytest.mark.parametrize(
        "name",
        ["create_account", "update-account", "createLead", "send_email", "close_case"],
    )
    def test_write_verbs_are_writes(self, name):
        assert classify_by_name(name) == "write"

    @pytest.mark.parametrize("name", ["delete_account", "purge-records", "removeContact"])
    def test_destructive_verbs_are_destructive(self, name):
        assert classify_by_name(name) == "destructive"

    def test_a_write_verb_anywhere_beats_a_leading_read_verb(self):
        """`get_and_update_account` starts with "get". It is not a read."""
        assert classify_by_name("get_and_update_account") == "write"

    def test_a_destructive_verb_anywhere_wins(self):
        assert classify_by_name("get_and_delete_account") == "destructive"

    def test_an_unrecognised_name_is_unknown_not_read(self):
        assert classify_by_name("frobnicate_widget") == "unknown"

    def test_naming_styles_are_all_understood(self):
        for name in ("get-associated-opportunities", "get_associated_opportunities",
                     "getAssociatedOpportunities"):
            assert classify_by_name(name) == "read"


class TestClassificationPrecedence:
    def test_deployment_policy_is_authoritative(self):
        """An operator saying "this is a write" ends the discussion, whatever
        the name suggests and whatever the server claims."""
        crm = connection(
            tool_policies={"get_account": MCPToolPolicy(operation="write")}
        )
        assert classify_tool("get_account", connection=crm) == "write"

    def test_a_server_cannot_declare_a_delete_tool_read_only(self):
        """The attack this precedence exists for. A compromised or careless
        server claiming `readOnlyHint: true` on `delete_account` must not be
        able to lower its own classification — the MCP spec says as much."""
        assert classify_tool(
            "delete_account", annotations=Annotations(read_only=True)
        ) == "destructive"

    def test_annotations_can_raise_a_classification(self):
        """Trusting a server that says "I am more dangerous than I look" is
        safe in the direction that matters."""
        assert classify_tool(
            "frobnicate_widget", annotations=Annotations(destructive=True)
        ) == "destructive"

    def test_a_read_only_hint_is_accepted_for_a_read_shaped_name(self):
        assert classify_tool(
            "get_account", annotations=Annotations(read_only=True)
        ) == "read"

    def test_annotations_are_ignored_when_the_deployment_has_spoken(self):
        crm = connection(
            tool_policies={"anything": MCPToolPolicy(operation="read")}
        )
        assert classify_tool(
            "anything", connection=crm, annotations=Annotations(destructive=True)
        ) == "read"


class TestWriteDefinition:
    def test_unknown_counts_as_a_write(self):
        """A tool nobody has classified is exactly the one that must not run
        unattended against a customer's system."""
        assert is_write("unknown") is True

    def test_only_read_is_not_a_write(self):
        assert is_write("read") is False
        assert is_write("write") is True
        assert is_write("destructive") is True


class TestGate:
    def test_a_read_runs_without_approval(self):
        decision = evaluate(connection=connection(), tool_name="get_account")
        assert decision.allowed is True
        assert decision.requires_approval is False

    def test_a_write_needs_approval_by_default(self):
        decision = evaluate(connection=connection(), tool_name="create_lead")
        assert decision.allowed is False
        assert decision.code == "MCP_APPROVAL_REQUIRED"
        with pytest.raises(MCPApprovalRequired):
            decision.raise_if_denied()

    def test_a_write_runs_once_approval_is_satisfied(self):
        decision = evaluate(
            connection=connection(),
            tool_name="create_lead",
            approval_satisfied=True,
        )
        assert decision.allowed is True

    def test_a_read_only_connection_refuses_writes_outright(self):
        """Not "needs approval" — refused. A workflow cannot grant itself write
        access to a connection the deployment made read-only."""
        decision = evaluate(
            connection=connection(write_policy="read_only"),
            tool_name="create_lead",
            approval_satisfied=True,
        )
        assert decision.allowed is False
        assert decision.code == "MCP_WRITE_NOT_PERMITTED"

    def test_an_allow_policy_permits_unattended_writes(self):
        """A legitimate decision an operator may make, explicitly."""
        decision = evaluate(
            connection=connection(write_policy="allow"), tool_name="create_lead"
        )
        assert decision.allowed is True

    def test_a_tool_outside_the_allowlist_is_refused(self):
        decision = evaluate(
            connection=connection(tool_allowlist=["get_account"]),
            tool_name="create_lead",
        )
        assert decision.allowed is False
        assert decision.code == "MCP_TOOL_NOT_ALLOWED"

    def test_the_denylist_beats_the_allowlist(self):
        decision = evaluate(
            connection=connection(
                tool_allowlist=["get_account"], tool_denylist=["get_account"]
            ),
            tool_name="get_account",
        )
        assert decision.allowed is False

    def test_a_role_restriction_is_enforced(self):
        crm = connection(allowed_roles=["admin"])
        assert evaluate(
            connection=crm, tool_name="get_account", user_role="consultant"
        ).allowed is False
        assert evaluate(
            connection=crm, tool_name="get_account", user_role="admin"
        ).allowed is True

    def test_a_per_tool_role_overrides_the_connection_role(self):
        crm = connection(
            allowed_roles=["consultant"],
            tool_policies={"create_lead": MCPToolPolicy(allowed_roles=["admin"])},
        )
        assert evaluate(
            connection=crm,
            tool_name="create_lead",
            user_role="consultant",
            approval_satisfied=True,
        ).allowed is False

    def test_a_per_tool_policy_can_waive_approval_for_one_low_risk_write(self):
        crm = connection(
            tool_policies={
                "create_followup_activity": MCPToolPolicy(requires_approval=False)
            }
        )
        assert evaluate(
            connection=crm, tool_name="create_followup_activity"
        ).allowed is True
        # …without waiving it for the others.
        assert evaluate(connection=crm, tool_name="create_lead").allowed is False

    def test_an_unknown_tool_is_gated_like_a_write(self):
        decision = evaluate(connection=connection(), tool_name="frobnicate_widget")
        assert decision.operation == "unknown"
        assert decision.allowed is False

    def test_the_refusal_says_what_to_do(self):
        decision = evaluate(connection=connection(), tool_name="create_lead")
        assert "Human Review" in decision.reason
        with pytest.raises(MCPPolicyError, match="human decision"):
            decision.raise_if_denied()


class TestRegistry:
    def test_a_secret_value_pasted_as_a_reference_is_rejected(self):
        """Configuration holds the NAME of an environment variable. A value
        pasted there would sit in whatever stores that configuration."""
        with pytest.raises(ValidationError, match="NAME of an environment variable"):
            MCPServerConnection(
                id="crm",
                command="python",
                environment_secret_refs={
                    "TOKEN": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.actual.secret"
                },
            )

    def test_a_valid_reference_is_accepted(self):
        crm = MCPServerConnection(
            id="crm",
            command="python",
            environment_secret_refs={"TOKEN": "DYNAMICS_CLIENT_SECRET"},
        )
        assert crm.environment_secret_refs["TOKEN"] == "DYNAMICS_CLIENT_SECRET"

    def test_secrets_resolve_from_the_environment_at_launch(self, monkeypatch):
        monkeypatch.setenv("DYNAMICS_CLIENT_SECRET", "real-secret")
        crm = MCPServerConnection(
            id="crm",
            command="python",
            environment_secret_refs={"DYNAMICS_CLIENT_SECRET": "DYNAMICS_CLIENT_SECRET"},
        )
        assert crm.resolve_environment()["DYNAMICS_CLIENT_SECRET"] == "real-secret"

    def test_a_missing_secret_is_omitted_not_faked(self, monkeypatch):
        monkeypatch.delenv("ABSENT_SECRET", raising=False)
        crm = MCPServerConnection(
            id="crm",
            command="python",
            environment_secret_refs={"TOKEN": "ABSENT_SECRET"},
        )
        assert "TOKEN" not in crm.resolve_environment()

    def test_the_builder_description_never_contains_a_secret(self, monkeypatch):
        monkeypatch.setenv("DYNAMICS_CLIENT_SECRET", "super-secret-value")
        crm = MCPServerConnection(
            id="crm",
            command="python",
            environment_secret_refs={"CLIENT_SECRET": "DYNAMICS_CLIENT_SECRET"},
        )
        described = crm.describe()
        assert "super-secret-value" not in str(described)
        # It does say the variable exists and is configured, which is what an
        # operator needs to see.
        assert described["credentials"][0]["configured"] is True

    def test_an_unsafe_server_id_is_rejected(self):
        with pytest.raises(ValidationError, match="lowercase alphanumeric"):
            MCPServerConnection(id="../../etc/passwd", command="python")

    def test_a_stdio_server_needs_a_command(self):
        with pytest.raises(ValidationError, match="needs a command"):
            MCPServerConnection(id="crm")

    def test_configured_servers_load_from_json(self):
        servers = load_servers(
            '[{"id":"crm","display_name":"CRM","command":"node",'
            '"args":["/opt/mcp/index.js"]}]'
        )
        assert servers["crm"].command == "node"

    def test_one_broken_definition_does_not_discard_the_others(self):
        servers = load_servers(
            '[{"id":"good","command":"python"},{"id":"BAD ID","command":"python"}]'
        )
        assert set(servers) == {"good"}

    def test_unparseable_configuration_yields_nothing_rather_than_raising(self):
        """A malformed server definition must not stop the API from starting;
        preflight reports it against the workflow that needs it."""
        assert load_servers("not json") == {}

    def test_the_registry_reports_health_per_server(self):
        registry = MCPServerRegistry()
        registry.add(connection())
        registry.record_health("crm", healthy=True, tool_count=12)
        assert registry.health("crm")["tool_count"] == 12
        assert registry.describe_all()[0]["status"]["healthy"] is True

    def test_an_unconfigured_server_names_what_is_available(self):
        registry = MCPServerRegistry()
        registry.add(connection())
        with pytest.raises(KeyError, match="available"):
            registry.require("nope")
