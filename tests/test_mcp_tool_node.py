"""The MCP Tool node and the integration service behind it.

The product claim under test is that **one generic node** reaches any business
system: the same node type, the same output contract, the same safety rules,
whether the server behind it is Dynamics, an ERP, or something that does not
exist yet. So the fixtures here deliberately use a nonsense server rather than
Dynamics — if any of this only worked for CRM, that would be the bug.

The safety claims are tested as adversarially as the policy gate itself: a write
must not run without approval, a retry must not duplicate a CRM record, and an
ambiguous failure must not be resolved by guessing.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.integrations.operations import (
    AmbiguousOperationFailure,
    ExternalOperationLedger,
    OperationInFlight,
)
from app.mcp.policy import MCPApprovalRequired
from app.mcp.registry import MCPServerConnection, MCPServerRegistry, MCPToolPolicy
from app.mcp.results import normalise_result, parse_json_text
from app.mcp.service import MCPIntegrationService, MCPToolError
from app.nodes.mcp_tool import MCPToolAgent


class FakeTool:
    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        annotations: Any = None,
        meta: dict | None = None,
    ):
        self.name = name
        self.title = None
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}
        self.outputSchema = output_schema
        self.annotations = annotations
        self.meta = meta


class FakeResult:
    """Stands in for the SDK's CallToolResult."""

    def __init__(self, *, structured=None, text: str = "", is_error: bool = False):
        self.structuredContent = structured
        self.content = [TextBlock(text)] if text else []
        self.isError = is_error


class TextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeClient:
    """An MCP client that records calls and returns scripted results."""

    def __init__(self, tools: list[FakeTool], results: dict[str, Any] | None = None):
        self._tools = tools
        self._results = results or {}
        self.calls: list[dict[str, Any]] = []
        self.running_servers = ("system",)

    async def list_tools(self, server: str):
        del server
        return self._tools

    async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
        self.calls.append(
            {"tool": name, "arguments": arguments, "server": server}
        )
        outcome = self._results.get(name, FakeResult(structured={"ok": True}))
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


def connection(**overrides) -> MCPServerConnection:
    base = {
        "id": "system",
        "display_name": "Business System",
        "command": "python",
        "args": ["-m", "whatever"],
    }
    base.update(overrides)
    return MCPServerConnection(**base)


def service(
    client: FakeClient, *, conn: MCPServerConnection | None = None
) -> MCPIntegrationService:
    registry = MCPServerRegistry()
    registry.add(conn or connection())
    return MCPIntegrationService(
        registry=registry, client=client, ledger=ExternalOperationLedger()
    )


DEFAULT_TOOLS = [
    FakeTool(
        "find_customer",
        description="Find a customer.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "customers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "customer_id": {"type": "string"},
                            "name": {"type": "string"},
                        },
                    },
                },
                "count": {"type": "integer"},
            },
        },
    ),
    FakeTool("create_record", description="Create a record."),
]


def node(config: dict, svc: MCPIntegrationService, node_id: str = "step") -> MCPToolAgent:
    instance = MCPToolAgent(node_id, config)
    instance.services = {"mcp": svc}
    return instance


async def run(instance: MCPToolAgent, run_id: str = "run-1", state=None):
    base = {"inputs": {"SYSTEM.run_id": run_id}, "node_outputs": {}}
    base.update(state or {})
    return await instance.run(base, instance.config.model_dump())


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

class TestDiscovery:
    @pytest.mark.asyncio
    async def test_tools_are_discovered_from_the_server_not_hardcoded(self):
        """A tool added to the MCP server appears in the Builder with no change
        here and none in the frontend — the entire reason MCP is the extension
        mechanism."""
        svc = service(FakeClient(DEFAULT_TOOLS))
        tools = await svc.discover_tools("system")
        assert [tool["name"] for tool in tools] == ["find_customer", "create_record"]

    @pytest.mark.asyncio
    async def test_each_tool_carries_its_classification(self):
        svc = service(FakeClient(DEFAULT_TOOLS))
        tools = {tool["name"]: tool for tool in await svc.discover_tools("system")}
        assert tools["find_customer"]["operation"] == "read"
        assert tools["create_record"]["operation"] == "write"
        assert tools["create_record"]["external_action"] is True
        assert tools["create_record"]["requires_approval"] is True

    @pytest.mark.asyncio
    async def test_the_declared_output_schema_becomes_mappable_paths(self):
        """So an MCP result is picked from a typed tree like any other node's
        output, instead of being a JSON string somebody parses."""
        svc = service(FakeClient(DEFAULT_TOOLS))
        tool = await svc.find_tool("system", "find_customer")
        paths = {field["path"] for field in tool["output_fields"]}
        assert "customers" in paths
        assert "customers.items.customer_id" in paths
        assert "count" in paths

    @pytest.mark.asyncio
    async def test_a_tool_outside_the_allowlist_is_not_even_offered(self):
        svc = service(
            FakeClient(DEFAULT_TOOLS),
            conn=connection(tool_allowlist=["find_customer"]),
        )
        assert [t["name"] for t in await svc.discover_tools("system")] == [
            "find_customer"
        ]

    @pytest.mark.asyncio
    async def test_an_unreachable_server_is_reported_not_crashed(self):
        class DeadClient(FakeClient):
            async def list_tools(self, server):
                raise ConnectionError("server is not running")

        svc = service(DeadClient([]))
        with pytest.raises(MCPToolError) as caught:
            await svc.discover_tools("system")
        assert caught.value.code == "MCP_SERVER_UNAVAILABLE"
        assert caught.value.retryable is True
        assert svc.registry.health("system")["healthy"] is False

    @pytest.mark.asyncio
    async def test_health_check_reports_the_tool_count(self):
        svc = service(FakeClient(DEFAULT_TOOLS))
        health = await svc.health_check("system")
        assert health == {
            "server_id": "system",
            "healthy": True,
            "tool_count": 2,
            "error": None,
        }

    def test_the_server_list_never_leaks_a_credential(self, monkeypatch):
        monkeypatch.setenv("SOME_SECRET", "super-secret")
        svc = service(
            FakeClient([]),
            conn=connection(environment_secret_refs={"TOKEN": "SOME_SECRET"}),
        )
        assert "super-secret" not in str(svc.describe_servers())


# --------------------------------------------------------------------------
# Result normalisation
# --------------------------------------------------------------------------

class TestResultNormalisation:
    def test_structured_content_is_used_when_present(self):
        result = normalise_result(FakeResult(structured={"customers": [], "count": 0}))
        assert result.is_structured is True
        assert result.source == "structuredContent"

    def test_json_serialised_into_text_is_parsed(self):
        """The common reality for servers that predate structured output — the
        reference Dynamics implementation among them."""
        result = normalise_result(FakeResult(text='{"customers": [], "count": 0}'))
        assert result.is_structured is True
        assert result.source == "text_json"
        assert result.data["count"] == 0

    def test_prose_is_left_as_text_and_reported_as_unstructured(self):
        """Said plainly rather than papered over: an author seeing "no fields
        to map" will go and fix the tool's schema, which is the right outcome."""
        result = normalise_result(FakeResult(text="Sorry, I could not find that."))
        assert result.is_structured is False
        assert result.text.startswith("Sorry")

    def test_a_bare_list_is_wrapped_so_mapping_paths_stay_stable(self):
        result = normalise_result(FakeResult(text='[{"id": 1}, {"id": 2}]'))
        assert result.data["count"] == 2
        assert len(result.data["items"]) == 2

    def test_an_oversized_payload_is_not_parsed(self):
        data, reason = parse_json_text('{"x": "' + "y" * 5000 + '"}', max_bytes=1000)
        assert data is None
        assert reason == "too_large"

    def test_a_pathologically_nested_payload_is_rejected(self):
        deep = "[" * 40 + "]" * 40
        data, reason = parse_json_text(deep, max_bytes=100_000)
        assert data is None
        assert reason == "too_deep"

    def test_prose_that_is_not_json_is_never_run_through_the_parser(self):
        data, reason = parse_json_text("Not JSON at all", max_bytes=1000)
        assert (data, reason) == (None, "not_json")

    def test_a_scalar_is_not_invented_into_an_object(self):
        """Wrapping `42` as {"value": 42} would invent a key the server never
        declared."""
        data, reason = parse_json_text("42", max_bytes=1000)
        assert data is None


# --------------------------------------------------------------------------
# The node
# --------------------------------------------------------------------------

class TestReadCalls:
    @pytest.mark.asyncio
    async def test_a_read_returns_typed_data(self):
        client = FakeClient(
            DEFAULT_TOOLS,
            {
                "find_customer": FakeResult(
                    structured={
                        "customers": [{"customer_id": "C1", "name": "ABC"}],
                        "count": 1,
                    }
                )
            },
        )
        output = await run(
            node(
                {"server_id": "system", "tool": "find_customer",
                 "arguments": {"name": "ABC"}},
                service(client),
            )
        )
        assert output["status"] == "ok"
        assert output["operation"] == "read"
        assert output["data"]["customers"][0]["customer_id"] == "C1"

    @pytest.mark.asyncio
    async def test_count_and_found_are_lifted_out_for_routing_rules(self):
        """"Did the CRM know this customer?" is what a rule asks; it should not
        need a Transform step to answer."""
        client = FakeClient(
            DEFAULT_TOOLS,
            {"find_customer": FakeResult(structured={"customers": [{"customer_id": "C1"}], "count": 1})},
        )
        output = await run(
            node({"server_id": "system", "tool": "find_customer",
                  "arguments": {"name": "ABC"}}, service(client))
        )
        assert output["count"] == 1
        assert output["found"] is True
        assert output["first"] == {"customer_id": "C1"}

    @pytest.mark.asyncio
    async def test_an_empty_result_is_found_false_not_an_error(self):
        client = FakeClient(
            DEFAULT_TOOLS,
            {"find_customer": FakeResult(structured={"customers": [], "count": 0})},
        )
        output = await run(
            node({"server_id": "system", "tool": "find_customer",
                  "arguments": {"name": "Nobody"}}, service(client))
        )
        assert output["found"] is False
        assert output["status"] == "ok"

    @pytest.mark.asyncio
    async def test_an_argument_that_resolved_to_nothing_skips_the_call(self):
        """The upstream lookup found no account, so there is no id to fetch
        opportunities for. Calling with null would produce a confusing server
        error; skipping reports the truth."""
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "system", "tool": "find_customer",
                 "arguments": {"name": None}},
                service(client),
            )
        )
        assert output["status"] == "skipped"
        assert output["found"] is False
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_a_structured_server_error_becomes_a_routable_status(self):
        client = FakeClient(
            DEFAULT_TOOLS,
            {
                "find_customer": FakeResult(
                    structured={
                        "error": {
                            "code": "CRM_RECORD_NOT_FOUND",
                            "message": "No such account.",
                            "retryable": False,
                            "suggested_action": "Send to a person.",
                        }
                    },
                    is_error=True,
                )
            },
        )
        output = await run(
            node(
                {"server_id": "system", "tool": "find_customer",
                 "arguments": {"name": "X"}, "fail_on_error": False},
                service(client),
            )
        )
        assert output["status"] == "error"
        assert output["error_code"] == "CRM_RECORD_NOT_FOUND"
        assert output["suggested_action"] == "Send to a person."
        assert output["found"] is False

    @pytest.mark.asyncio
    async def test_fail_on_error_raises_when_the_author_wants_a_hard_stop(self):
        client = FakeClient(DEFAULT_TOOLS, {"find_customer": RuntimeError("boom")})
        with pytest.raises(RuntimeError, match="MCPToolAgent"):
            await run(
                node(
                    {"server_id": "system", "tool": "find_customer",
                     "arguments": {"name": "X"}, "fail_on_error": True},
                    service(client),
                )
            )

    @pytest.mark.asyncio
    async def test_an_unknown_tool_names_what_is_available(self):
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "system", "tool": "no_such_tool",
                 "fail_on_error": False},
                service(client),
            )
        )
        assert output["error_code"] == "MCP_UNKNOWN_TOOL"
        assert "find_customer" in output["error"]

    @pytest.mark.asyncio
    async def test_an_unconfigured_server_is_reported_clearly(self):
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "nonexistent", "tool": "find_customer",
                 "fail_on_error": False},
                service(client),
            )
        )
        assert output["error_code"] == "MCP_SERVER_NOT_CONFIGURED"


class TestWriteSafety:
    @pytest.mark.asyncio
    async def test_a_write_is_refused_without_a_human_review(self):
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "system", "tool": "create_record",
                 "arguments": {"subject": "x"}, "fail_on_error": False},
                service(client),
            )
        )
        assert output["status"] == "needs_approval"
        assert client.calls == [], "the write reached the server anyway"

    @pytest.mark.asyncio
    async def test_a_write_runs_after_an_upstream_human_approval(self):
        """Read from the run's own completed outputs — a node cannot vouch for
        its own approval."""
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "system", "tool": "create_record",
                 "arguments": {"subject": "x"}},
                service(client),
            ),
            state={"node_outputs": {"gate": {"decision": "approve"}}},
        )
        assert output["status"] == "ok"
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_a_rejection_does_not_count_as_approval(self):
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "system", "tool": "create_record",
                 "arguments": {"subject": "x"}, "fail_on_error": False},
                service(client),
            ),
            state={"node_outputs": {"gate": {"decision": "reject"}}},
        )
        assert output["status"] == "needs_approval"

    @pytest.mark.asyncio
    async def test_an_explicit_unattended_write_is_permitted(self):
        """A decision the author made and can see on the canvas — that is the
        requirement, not that it be forbidden."""
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "system", "tool": "create_record",
                 "arguments": {"subject": "x"}, "allow_unattended_write": True},
                service(client),
            )
        )
        assert output["status"] == "ok"

    @pytest.mark.asyncio
    async def test_a_node_cannot_grant_itself_write_access(self):
        """`allow_unattended_write` waives the *approval*, not the connection's
        policy. A read-only connection still refuses."""
        client = FakeClient(DEFAULT_TOOLS)
        output = await run(
            node(
                {"server_id": "system", "tool": "create_record",
                 "arguments": {"subject": "x"},
                 "allow_unattended_write": True, "fail_on_error": False},
                service(client, conn=connection(write_policy="read_only")),
            )
        )
        assert output["status"] == "denied"
        assert output["error_code"] == "MCP_WRITE_NOT_PERMITTED"
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_a_retried_run_does_not_write_twice(self):
        """The case this exists for: a run retried after a failure downstream of
        the write must not create a second CRM record."""
        client = FakeClient(DEFAULT_TOOLS)
        svc = service(client)
        config = {
            "server_id": "system",
            "tool": "create_record",
            "arguments": {"subject": "Enquiry"},
            "allow_unattended_write": True,
        }
        first = await run(node(config, svc), run_id="run-1")
        second = await run(node(config, svc), run_id="run-1")

        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_two_different_runs_both_write(self):
        """Two customers getting the same follow-up task are not duplicates."""
        client = FakeClient(DEFAULT_TOOLS)
        svc = service(client)
        config = {
            "server_id": "system",
            "tool": "create_record",
            "arguments": {"subject": "Enquiry"},
            "allow_unattended_write": True,
        }
        await run(node(config, svc), run_id="run-1")
        await run(node(config, svc), run_id="run-2")
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_a_write_timeout_is_ambiguous_and_blocks_the_retry(self):
        """§30: where the outcome cannot be known, stop and require
        reconciliation rather than blindly repeating an uncertain write."""
        client = FakeClient(DEFAULT_TOOLS, {"create_record": TimeoutError("timed out")})
        svc = service(client)
        config = {
            "server_id": "system",
            "tool": "create_record",
            "arguments": {"subject": "Enquiry"},
            "allow_unattended_write": True,
            "fail_on_error": True,
        }
        with pytest.raises(RuntimeError):
            await run(node(config, svc), run_id="run-1")

        # The second attempt is refused with an actionable message, not retried.
        instance = node(config, svc)
        with pytest.raises(RuntimeError, match="check the record"):
            await run(instance, run_id="run-1")

    @pytest.mark.asyncio
    async def test_a_definitive_failure_frees_the_key_for_a_corrected_retry(self):
        """A rejected field is not a "maybe wrote" — after fixing it, the retry
        must not be refused as a duplicate."""
        attempts = {"count": 0}

        def flaky():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("field rejected")
            return FakeResult(structured={"id": "new"})

        client = FakeClient(DEFAULT_TOOLS, {"create_record": flaky})
        svc = service(client)
        config = {
            "server_id": "system",
            "tool": "create_record",
            "arguments": {"subject": "Enquiry"},
            "allow_unattended_write": True,
            "fail_on_error": False,
        }
        failed = await run(node(config, svc), run_id="run-1")
        assert failed["status"] == "error"

        recovered = await run(node(config, svc), run_id="run-1")
        assert recovered["status"] == "ok"
        assert recovered["deduplicated"] is False

    @pytest.mark.asyncio
    async def test_a_role_restriction_is_enforced_at_call_time(self):
        client = FakeClient(DEFAULT_TOOLS)
        svc = service(client, conn=connection(allowed_roles=["admin"]))
        with pytest.raises(Exception) as caught:
            await svc.call(
                server_id="system",
                tool_name="find_customer",
                arguments={"name": "X"},
                user_role="consultant",
            )
        assert "may not call" in str(caught.value)


class TestGenericAcrossServers:
    """The product claim: one node type, any system."""

    @pytest.mark.asyncio
    async def test_the_output_shape_is_identical_across_servers_and_tools(self):
        erp_tools = [FakeTool("get_stock_level", description="Stock.")]
        crm = service(FakeClient(DEFAULT_TOOLS))
        erp = service(
            FakeClient(erp_tools, {"get_stock_level": FakeResult(structured={"units": 5})}),
            conn=connection(id="erp", display_name="ERP"),
        )

        from_crm = await run(
            node({"server_id": "system", "tool": "find_customer",
                  "arguments": {"name": "ABC"}}, crm)
        )
        from_erp = await run(
            node({"server_id": "erp", "tool": "get_stock_level",
                  "arguments": {"sku": "X"}}, erp)
        )
        assert set(from_crm) == set(from_erp)
        assert from_erp["data"]["units"] == 5

    def test_mcp_node_config_remains_provider_neutral(self):
        """A `dynamics_url` or `crm_entity` field here would mean the next
        system needs a code change.

        Stated as the invariant rather than as a frozen field list: the promise
        is that no vendor, product or transport detail reaches this config, not
        that the config never gains a field. A generic execution control — a
        latency budget, a retry count — names no vendor and is allowed to
        appear without a test edit. An earlier exact-set assertion failed on
        `timeout_seconds`, which was an accidental implementation freeze rather
        than the design constraint doing its job.
        """
        from app.nodes.mcp_tool import MCPToolConfig

        fields = set(MCPToolConfig.model_fields)

        vendors = (
            "dynamics", "salesforce", "sap", "netsuite", "hubspot", "oracle",
            "workday", "servicenow", "zendesk", "odata", "dataverse",
        )
        transports = ("url", "endpoint", "host", "port", "credential", "api_key",
                      "token", "secret", "password", "connection_string")
        entities = ("entity", "table", "object", "collection", "sobject", "client")

        for field in fields:
            lowered = field.lower()
            for banned in vendors + transports:
                assert banned not in lowered, (
                    f"{field!r} ties this node to one system or its transport; "
                    "that belongs to the connection, not the workflow"
                )
            # `crm_entity`/`sap_client` style: an entity word is only a problem
            # when it names the *provider's* data model rather than the call.
            for word in entities:
                assert not lowered.endswith(f"_{word}"), f"{field!r} names a provider data model"

        # The generic call contract every server shares must still be present.
        assert {"server_id", "tool", "arguments"} <= fields

    def test_there_is_no_crm_specific_node_type(self):
        """§1: the whole point. No DynamicsGetAccountNode, ever."""
        import app.nodes  # noqa: F401
        from app.nodes.registry import NodeRegistry

        crm_shaped = [
            name
            for name in NodeRegistry._registry
            if any(
                token in name.lower()
                for token in ("dynamics", "crm", "salesforce", "dataverse")
            )
        ]
        assert crm_shaped == []


class TestAudit:
    @pytest.mark.asyncio
    async def test_an_invocation_is_recorded_without_customer_data(self):
        """§27: an audit collection full of customer names would be a second,
        less protected copy of the CRM."""
        recorded: list[dict] = []

        class FakeCollection:
            async def insert_one(self, document):
                recorded.append(document)

        class FakeDB:
            def __getitem__(self, name):
                return FakeCollection()

        client = FakeClient(
            DEFAULT_TOOLS,
            {
                "find_customer": FakeResult(
                    structured={
                        "customers": [
                            {"customer_id": "C1", "name": "Thomas Becker"}
                        ],
                        "count": 1,
                    }
                )
            },
        )
        await service(client).call(
            server_id="system",
            tool_name="find_customer",
            arguments={"name": "ABC Chemicals"},
            run_id="run-1",
            node_id="find_step",
            audit_db=FakeDB(),
        )

        assert len(recorded) == 1
        entry = recorded[0]
        assert entry["tool_name"] == "find_customer"
        assert entry["operation_class"] == "read"
        assert entry["status"] == "completed"
        assert entry["run_id"] == "run-1"
        # Shape only: the field NAMES are recorded, never the values.
        assert "Thomas Becker" not in str(entry)
        assert "ABC Chemicals" not in str(entry)

    @pytest.mark.asyncio
    async def test_a_denied_call_is_audited_too(self):
        recorded: list[dict] = []

        class FakeCollection:
            async def insert_one(self, document):
                recorded.append(document)

        class FakeDB:
            def __getitem__(self, name):
                return FakeCollection()

        with pytest.raises(MCPApprovalRequired):
            await service(FakeClient(DEFAULT_TOOLS)).call(
                server_id="system",
                tool_name="create_record",
                arguments={"subject": "x"},
                run_id="run-1",
                node_id="write_step",
                audit_db=FakeDB(),
            )
        assert recorded[0]["status"] == "denied"

    @pytest.mark.asyncio
    async def test_an_audit_failure_never_fails_the_workflow(self):
        class BrokenDB:
            def __getitem__(self, name):
                raise RuntimeError("mongo is down")

        result = await service(FakeClient(DEFAULT_TOOLS)).call(
            server_id="system",
            tool_name="find_customer",
            arguments={"name": "X"},
            run_id="run-1",
            audit_db=BrokenDB(),
        )
        assert result["operation"] == "read"


class TestNodeMetadata:
    def test_it_is_a_core_primitive_marked_as_an_external_action(self):
        import app.nodes  # noqa: F401
        from app.nodes.registry import NodeRegistry

        entry = next(
            item
            for item in NodeRegistry.manifest()
            if item["type_name"] == "MCPToolAgent"
        )
        assert entry["family"] == "core"
        assert entry["execution_kind"] == "external"
        assert entry["external_action"] is True
        assert entry["uses_ai"] is False

    def test_it_declares_the_mcp_service_as_required(self):
        assert MCPToolAgent.required_services({}) == {"mcp"}

    def test_result_paths_are_authorised_as_server_defined_prefixes(self):
        """Honest about the boundary: the sub-shape comes from the server, which
        preflight deliberately does not contact."""
        fields = MCPToolAgent.preflight_output_fields({})
        assert "data.*" in fields
        assert "first.*" in fields


class TestTimeoutBehaviour:
    """A timeout is only a guarantee if the thing it interrupted actually stops.

    `asyncio.timeout` gives the *caller* a bounded wait. The production
    question is what happened to the operation underneath it: a request left
    running against Dynamics, a task nobody awaited, or a client left in a
    state the next call inherits would all satisfy "we added a timeout" while
    failing the property anyone actually wants.
    """

    def _service(self, client) -> MCPIntegrationService:
        registry = MCPServerRegistry()
        registry.add(connection(timeout_seconds=30))
        return MCPIntegrationService(
            registry=registry, client=client, ledger=ExternalOperationLedger()
        )

    @pytest.mark.asyncio
    async def test_the_node_timeout_overrides_a_longer_connection_timeout(self):
        import asyncio

        class Hanging(FakeClient):
            async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
                self.calls.append({"tool": name, "timeout_seconds": timeout_seconds})
                await asyncio.sleep(30)

        client = Hanging([FakeTool("read_thing")])
        instance = node(
            {"server_id": "system", "tool": "read_thing",
             "fail_on_error": False, "timeout_seconds": 0.2},
            self._service(client),
        )
        started = asyncio.get_running_loop().time()
        result = await run(instance)
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 5, f"the connection's 30s won over the node's 0.2s ({elapsed:.1f}s)"
        assert result["status"] == "error"
        assert result["error_code"] == "MCP_TOOL_TIMEOUT"
        # The bound is passed down as well as enforced above, so a client that
        # honours it can stop early on its own.
        assert client.calls[0]["timeout_seconds"] == 0.2

    @pytest.mark.asyncio
    async def test_a_timed_out_call_is_cancelled_and_leaves_nothing_running(self):
        import asyncio

        class Observed(FakeClient):
            def __init__(self, tools):
                super().__init__(tools)
                self.cancelled = False
                self.completed = False

            async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
                del name, arguments, server, timeout_seconds
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
                self.completed = True

        client = Observed([FakeTool("read_thing")])
        service = self._service(client)
        before = len(asyncio.all_tasks())

        with pytest.raises(MCPToolError) as raised:
            await service.call(
                server_id="system", tool_name="read_thing", arguments={},
                timeout_override=0.2,
            )

        assert raised.value.code == "MCP_TOOL_TIMEOUT"
        # The underlying operation was cancelled, not abandoned mid-flight.
        assert client.cancelled is True
        assert client.completed is False
        # Give the loop a turn, then confirm nothing was left behind.
        await asyncio.sleep(0)
        assert len(asyncio.all_tasks()) <= before

    @pytest.mark.asyncio
    async def test_the_next_call_after_a_timeout_still_succeeds(self):
        """A timeout must not poison the client or the connection for whatever
        runs next — including the retry the error tells you to make."""
        import asyncio

        class SlowThenFast(FakeClient):
            def __init__(self, tools):
                super().__init__(tools)
                self.attempts = 0

            async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
                del arguments, server, timeout_seconds
                self.attempts += 1
                if self.attempts == 1:
                    await asyncio.sleep(30)
                return FakeResult(structured={"units": 5})

        client = SlowThenFast([FakeTool("read_thing")])
        service = self._service(client)

        with pytest.raises(MCPToolError):
            await service.call(server_id="system", tool_name="read_thing",
                               arguments={}, timeout_override=0.2)

        recovered = await service.call(
            server_id="system", tool_name="read_thing", arguments={},
            timeout_override=5,
        )
        assert recovered["data"] == {"units": 5}
        assert client.attempts == 2

    @pytest.mark.asyncio
    async def test_a_timed_out_read_is_retryable_and_says_so(self):
        import asyncio

        class Hanging(FakeClient):
            async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
                await asyncio.sleep(30)

        service = self._service(Hanging([FakeTool("read_thing")]))
        with pytest.raises(MCPToolError) as raised:
            await service.call(server_id="system", tool_name="read_thing",
                               arguments={}, timeout_override=0.2)
        assert raised.value.retryable is True
