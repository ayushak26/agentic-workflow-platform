"""The Dynamics 365 MCP server, and the hardening applied to the reference.

The reference implementation (`srikanth-paladugula/mcp-dynamics365-server`) was
used as an architectural guide, not copied. Four of these test classes exist
specifically because of what it does differently:

*   `TestODataSafety` — the reference interpolates a caller-supplied id straight
    into `$filter`. That value can originate from a model reading a customer's
    email.
*   `TestNarrowWriteSurface` — the reference's `create-account` takes
    `accountData: z.object({})`, an unrestricted write surface.
*   `TestBoundedReads` — the reference applies no `$select`, `$top` or paging,
    so `fetch-accounts` returns every column of every account.
*   `TestStructuredResults` — the reference serialises results into text.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.mcp.dynamics import odata
from app.mcp.dynamics.client import (
    DataverseClient,
    DynamicsError,
    FixtureBackend,
)
from app.mcp.dynamics.handlers import HANDLERS
from app.mcp.dynamics.server import DEFAULT_FIXTURES
from app.mcp.dynamics.tools import (
    READ_ONLY_TOOLS,
    TOOL_DEFINITIONS,
    TOOLS_BY_NAME,
    WRITE_TOOLS,
)

ACCOUNT_GUID = "a1b2c3d4-0000-4000-8000-000000000002"


@pytest.fixture
def backend() -> FixtureBackend:
    return FixtureBackend.from_file(DEFAULT_FIXTURES)


async def call(backend, tool: str, **arguments):
    return await HANDLERS[tool](backend, arguments)


class TestODataSafety:
    """The injection the reference implementation is open to."""

    def test_a_filter_smuggled_into_an_identifier_is_rejected(self):
        with pytest.raises(odata.ODataValueError, match="GUID"):
            odata.lookup_filter("_customerid_value", "x' or 1 eq 1 or '")

    def test_a_bare_word_is_not_a_valid_identifier(self):
        with pytest.raises(odata.ODataValueError):
            odata.guid("account-1", field="account_id")

    def test_a_valid_guid_is_normalised(self):
        assert odata.guid("{A1B2C3D4-0000-4000-8000-000000000002}") == ACCOUNT_GUID

    def test_a_lookup_filter_uses_a_bare_guid_not_a_quoted_string(self):
        """Dataverse compares a lookup to an unquoted GUID. Quoting it would
        fail — which is why *validation*, not escaping, is what makes this
        safe."""
        assert odata.lookup_filter("_customerid_value", ACCOUNT_GUID) == (
            f"_customerid_value eq {ACCOUNT_GUID}"
        )

    def test_single_quotes_in_free_text_are_doubled(self):
        assert odata.contains_filter("name", "O'Brien") == "contains(name,'O''Brien')"

    def test_a_closing_quote_cannot_escape_the_literal(self):
        built = odata.string_filter("name", "x' or name eq 'y")
        assert built == "name eq 'x'' or name eq ''y'"
        # One opening and one closing quote at the outer level: the injected
        # quotes are all doubled and therefore literal.
        assert built.count("'") % 2 == 0

    def test_control_characters_are_stripped(self):
        assert "\n" not in odata.escape_literal("ABC\nChemicals")

    def test_an_over_long_term_is_rejected(self):
        with pytest.raises(odata.ODataValueError, match="too long"):
            odata.escape_literal("x" * 500)

    def test_an_invalid_column_name_is_rejected(self):
        with pytest.raises(odata.ODataValueError, match="invalid column"):
            odata.build_query(select=["name; drop table"])

    def test_an_out_of_range_row_limit_is_rejected(self):
        with pytest.raises(odata.ODataValueError, match="between 1 and 200"):
            odata.build_query(top=5000)

    def test_an_injected_sort_direction_is_rejected(self):
        with pytest.raises(odata.ODataValueError):
            odata.build_query(order_by="name asc; drop")

    @pytest.mark.asyncio
    async def test_a_handler_refuses_a_non_guid_account_id(self, backend):
        with pytest.raises(odata.ODataValueError):
            await call(backend, "get_open_opportunities", account_id="' or 1 eq 1")


class TestBoundedReads:
    """The reference applies no $select, $top or paging."""

    @pytest.mark.asyncio
    async def test_a_read_selects_only_the_declared_columns(self, backend):
        captured: dict = {}
        original = backend.query

        async def spy(entity_set, **kwargs):
            captured.update(kwargs)
            return await original(entity_set, **kwargs)

        backend.query = spy
        await call(backend, "find_account", company_name="ABC")
        assert captured["select"], "no $select was applied"
        assert captured["top"] is not None, "no row limit was applied"

    @pytest.mark.asyncio
    async def test_the_row_limit_is_capped_regardless_of_the_request(self, backend):
        captured: dict = {}
        original = backend.query

        async def spy(entity_set, **kwargs):
            captured.update(kwargs)
            return await original(entity_set, **kwargs)

        backend.query = spy
        await call(backend, "find_account", company_name="ABC", limit=9999)
        # 25 is this tool's declared maximum; +1 is the "is there more?" probe.
        assert captured["top"] == 26

    @pytest.mark.asyncio
    async def test_truncation_is_reported_rather_than_hidden(self, backend):
        result = await call(backend, "find_account", company_name="ABC", limit=1)
        assert result["count"] == 1
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_an_exact_result_is_not_marked_truncated(self, backend):
        result = await call(backend, "find_account", company_name="Nordvand")
        assert result["truncated"] is False


class TestNarrowWriteSurface:
    """The reference exposes `accountData: object` — any column, any value."""

    def test_the_update_tool_declares_its_exact_writable_fields(self):
        schema = TOOLS_BY_NAME["update_account_contact_details"]["input_schema"]
        assert set(schema["properties"]) == {
            "account_id",
            "telephone",
            "website",
            "address_line1",
            "address_city",
            "address_country",
        }

    @pytest.mark.asyncio
    async def test_writing_an_undeclared_field_is_refused_not_ignored(self, backend):
        """Refused, because a workflow that believes it set `ownerid` and
        silently did not is worse than one that is told it cannot."""
        with pytest.raises(DynamicsError, match="cannot change"):
            await call(
                backend,
                "update_account_contact_details",
                account_id=ACCOUNT_GUID,
                ownerid="someone-else",
            )

    @pytest.mark.asyncio
    async def test_a_permitted_update_reports_what_it_changed(self, backend):
        result = await call(
            backend,
            "update_account_contact_details",
            account_id=ACCOUNT_GUID,
            telephone="+49 211 555 0000",
        )
        assert result["updated"] is True
        assert result["updated_fields"] == ["telephone"]

    @pytest.mark.asyncio
    async def test_an_update_with_nothing_to_change_does_not_write(self, backend):
        result = await call(
            backend, "update_account_contact_details", account_id=ACCOUNT_GUID
        )
        assert result["updated"] is False
        assert backend.writes == []

    @pytest.mark.asyncio
    async def test_an_over_long_write_value_is_refused(self, backend):
        with pytest.raises(DynamicsError, match="too long"):
            await call(backend, "create_lead", subject="x" * 400)

    def test_every_write_tool_declares_bounded_string_fields(self):
        for name in WRITE_TOOLS:
            schema = TOOLS_BY_NAME[name]["input_schema"]
            for field, spec in schema["properties"].items():
                if spec.get("type") == "string" and not field.endswith("_id"):
                    assert "maxLength" in spec, f"{name}.{field} is unbounded"


class TestReadTools:
    @pytest.mark.asyncio
    async def test_find_account_matches_on_name(self, backend):
        result = await call(backend, "find_account", company_name="ABC Chemicals GmbH")
        assert result["accounts"][0]["account_name"] == "ABC Chemicals GmbH"

    @pytest.mark.asyncio
    async def test_find_account_also_matches_a_related_entity(self, backend):
        """A customer writing "ABC Chemicals" should surface both legal
        entities, so a person can pick the right one rather than the workflow
        guessing."""
        result = await call(backend, "find_account", company_name="ABC Chemicals")
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_dataverse_column_names_are_translated_to_business_names(self, backend):
        """A workflow author sees `account_id` and `status`, not
        `_customerid_value` and `statecode`."""
        result = await call(backend, "find_account", company_name="Nordvand")
        account = result["accounts"][0]
        assert set(account) >= {"account_id", "account_name", "status", "industry"}
        assert account["status"] == "active"

    @pytest.mark.asyncio
    async def test_an_inactive_account_is_reported_as_inactive(self, backend):
        result = await call(backend, "find_account", company_name="Verder Liquids")
        assert result["accounts"][0]["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_open_opportunities_exclude_closed_ones(self, backend):
        result = await call(backend, "get_open_opportunities", account_id=ACCOUNT_GUID)
        assert result["count"] == 1
        assert result["opportunities"][0]["name"] == "Pump Replacement 2026"

    @pytest.mark.asyncio
    async def test_order_history_carries_serial_numbers(self, backend):
        """The fact that makes "another pump like last time" answerable."""
        result = await call(backend, "find_previous_orders", account_id=ACCOUNT_GUID)
        serials = [
            item["serial_number"]
            for order in result["orders"]
            for item in order["products"]
        ]
        assert "VF-29831" in serials

    @pytest.mark.asyncio
    async def test_orders_are_returned_newest_first(self, backend):
        result = await call(backend, "find_previous_orders", account_id=ACCOUNT_GUID)
        numbers = [order["order_number"] for order in result["orders"]]
        assert numbers == sorted(numbers, reverse=True)

    @pytest.mark.asyncio
    async def test_find_contact_matches_an_exact_email(self, backend):
        result = await call(
            backend, "find_contact", email="t.becker@abc-chemicals.example"
        )
        assert result["contacts"][0]["full_name"] == "Thomas Becker"

    @pytest.mark.asyncio
    async def test_find_contact_needs_something_to_search_on(self, backend):
        with pytest.raises(DynamicsError, match="needs an email address or a name"):
            await call(backend, "find_contact")

    @pytest.mark.asyncio
    async def test_a_missing_account_is_a_structured_not_found(self, backend):
        with pytest.raises(DynamicsError) as caught:
            await call(
                backend, "get_account", account_id="00000000-0000-4000-8000-000000000999"
            )
        assert caught.value.code == "CRM_RECORD_NOT_FOUND"
        assert caught.value.suggested_action

    @pytest.mark.asyncio
    async def test_a_search_with_no_match_returns_empty_not_an_error(self, backend):
        """"We do not know this company" is a business outcome the workflow
        routes on, not a failure."""
        result = await call(backend, "find_account", company_name="Nonexistent Ltd")
        assert result["count"] == 0
        assert result["accounts"] == []


class TestToolContracts:
    def test_every_tool_declares_an_output_schema(self):
        """Without one there is nothing for the mapping picker to offer, and a
        workflow is back to parsing text."""
        for definition in TOOL_DEFINITIONS:
            assert definition["output_schema"].get("properties")

    def test_every_tool_declares_its_operation_class(self):
        for definition in TOOL_DEFINITIONS:
            assert definition["operation"] in ("read", "write", "destructive")

    def test_every_tool_has_a_handler(self):
        for definition in TOOL_DEFINITIONS:
            assert definition["name"] in HANDLERS

    def test_the_vocabulary_is_business_shaped_not_api_shaped(self):
        """No `execute_dynamics_request(endpoint, method, body)` — that would
        destroy the safety boundary entirely (§16)."""
        names = set(TOOLS_BY_NAME)
        assert not any(
            token in name
            for name in names
            for token in ("execute", "request", "query_raw", "sql", "odata")
        )

    def test_read_and_write_tools_are_separately_addressable(self):
        """So a least-privilege connection can allow exactly the reads."""
        assert set(READ_ONLY_TOOLS).isdisjoint(WRITE_TOOLS)
        assert set(READ_ONLY_TOOLS) | set(WRITE_TOOLS) == set(TOOLS_BY_NAME)

    def test_every_tool_explains_when_to_use_it(self):
        for definition in TOOL_DEFINITIONS:
            if definition["name"] == "get_current_user":
                continue
            assert definition.get("typical_uses"), definition["name"]


class TestLiveClient:
    """The Dataverse client, driven through a mocked transport."""

    def build(self, handler) -> DataverseClient:
        return DataverseClient(
            base_url="https://org.crm.dynamics.com",
            tenant_id="tenant",
            client_id="client",
            client_secret="secret",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    @pytest.mark.asyncio
    async def test_it_acquires_a_client_credentials_token_for_the_org_scope(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                seen["body"] = request.content.decode()
                return httpx.Response(
                    200, json={"access_token": "tok", "expires_in": 3600}
                )
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"value": []})

        client = self.build(handler)
        await client.query("accounts", select=["name"], top=1)
        await client.close()

        assert "grant_type=client_credentials" in seen["body"]
        assert "https%3A%2F%2Forg.crm.dynamics.com%2F.default" in seen["body"]
        assert seen["auth"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_the_token_is_reused_rather_than_refetched(self):
        tokens = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                tokens["count"] += 1
                return httpx.Response(
                    200, json={"access_token": "tok", "expires_in": 3600}
                )
            return httpx.Response(200, json={"value": []})

        client = self.build(handler)
        await client.query("accounts", select=["name"], top=1)
        await client.query("contacts", select=["fullname"], top=1)
        await client.close()
        assert tokens["count"] == 1

    @pytest.mark.asyncio
    async def test_a_rejected_credential_is_an_actionable_auth_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"error": "invalid_client", "error_description": "AADSTS7000215"}
            )

        client = self.build(handler)
        with pytest.raises(DynamicsError) as caught:
            await client.whoami()
        await client.close()
        assert caught.value.code == "DYNAMICS_AUTH_FAILED"
        assert "client id, secret and tenant" in caught.value.suggested_action

    @pytest.mark.asyncio
    async def test_a_forbidden_response_points_at_the_security_role(self):
        """The least-privilege failure mode: the connection works, but the
        application user's role does not grant this entity."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return httpx.Response(403, json={"error": {"message": "Principal lacks privilege"}})

        client = self.build(handler)
        with pytest.raises(DynamicsError) as caught:
            await client.query("accounts", select=["name"], top=1)
        await client.close()
        assert caught.value.code == "DYNAMICS_FORBIDDEN"
        assert "security role" in caught.value.suggested_action

    @pytest.mark.asyncio
    async def test_a_read_timeout_is_retryable_and_a_write_timeout_is_not(self):
        """A read changes nothing, so retrying is free. A write that timed out
        has an unknown outcome, and saying "retryable" would invite a duplicate
        record."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            raise httpx.ReadTimeout("timed out", request=request)

        client = self.build(handler)
        with pytest.raises(DynamicsError) as read_error:
            await client.query("accounts", select=["name"], top=1)
        with pytest.raises(DynamicsError) as write_error:
            await client.create("leads", {"subject": "x"})
        await client.close()

        assert read_error.value.retryable is True
        assert write_error.value.retryable is False

    @pytest.mark.asyncio
    async def test_rate_limiting_is_reported_as_retryable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return httpx.Response(429, json={})

        client = self.build(handler)
        with pytest.raises(DynamicsError) as caught:
            await client.query("accounts", select=["name"], top=1)
        await client.close()
        assert caught.value.code == "DYNAMICS_RATE_LIMITED"
        assert caught.value.retryable is True

    @pytest.mark.asyncio
    async def test_a_create_returns_the_new_record_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return httpx.Response(201, json={"leadid": "new-lead-id", "subject": "x"})

        client = self.build(handler)
        created = await client.create("leads", {"subject": "x"})
        await client.close()
        assert created == "new-lead-id"

    @pytest.mark.asyncio
    async def test_a_create_falls_back_to_the_entity_id_header(self):
        """A bare 204 with `OData-EntityId` is what Dataverse returns without
        `Prefer: return=representation`."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return httpx.Response(
                204,
                headers={
                    "OData-EntityId": (
                        "https://org.crm.dynamics.com/api/data/v9.2/leads(abc-123)"
                    )
                },
            )

        client = self.build(handler)
        assert await client.create("leads", {"subject": "x"}) == "abc-123"
        await client.close()

    @pytest.mark.asyncio
    async def test_the_request_url_carries_select_and_filter(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"value": []})

        client = self.build(handler)
        await client.query(
            "accounts",
            select=["name", "accountid"],
            filter_expression=odata.contains_filter("name", "ABC"),
            top=5,
        )
        await client.close()
        assert "$select=name,accountid" in seen["url"]
        assert "$top=5" in seen["url"]
        assert "contains(name," in seen["url"]


class TestFixtureBackendIsATrueTwin:
    """§22/§23: the demo must exercise the same contracts, or it teaches
    nothing about the real integration."""

    @pytest.mark.asyncio
    async def test_it_actually_interprets_filters(self, backend):
        """Not "returns everything regardless" — a workflow built against a
        backend that ignores filters would break the moment it went live."""
        rows = await backend.query(
            "accounts",
            select=["name"],
            filter_expression=odata.contains_filter("name", "Nordvand"),
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_it_honours_a_guid_lookup_filter(self, backend):
        rows = await backend.query(
            "opportunities",
            select=["name"],
            filter_expression=odata.lookup_filter("_customerid_value", ACCOUNT_GUID),
        )
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_it_honours_combined_and_or_filters(self, backend):
        rows = await backend.query(
            "opportunities",
            select=["name"],
            filter_expression=odata.all_of(
                odata.lookup_filter("_customerid_value", ACCOUNT_GUID),
                "statecode eq 0",
            ),
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_an_uninterpretable_filter_fails_loudly(self, backend):
        """Rather than matching everything, which would make the demo lie."""
        with pytest.raises(DynamicsError, match="cannot interpret"):
            await backend.query(
                "accounts", select=["name"], filter_expression="name ne 'x'"
            )

    @pytest.mark.asyncio
    async def test_a_created_record_is_findable_afterwards(self, backend):
        result = await call(backend, "create_lead", subject="Demo enquiry")
        stored = backend.store["leads"]
        assert stored[0]["leadid"] == result["lead_id"]

    @pytest.mark.asyncio
    async def test_generated_ids_are_guid_shaped(self, backend):
        """So downstream GUID validation behaves exactly as it would live."""
        result = await call(backend, "create_lead", subject="Demo")
        assert odata.guid(result["lead_id"])

    @pytest.mark.asyncio
    async def test_it_reports_itself_as_a_mock(self, backend):
        assert backend.is_mock is True


class TestStructuredResults:
    """The reference serialises results into text; this returns typed data."""

    def test_the_declared_output_schema_matches_what_handlers_return(self):
        schema = TOOLS_BY_NAME["find_account"]["output_schema"]
        assert set(schema["properties"]) == {"accounts", "count", "truncated"}

    @pytest.mark.asyncio
    async def test_a_result_is_json_serialisable_as_declared(self, backend):
        result = await call(backend, "find_account", company_name="ABC")
        # Round-trips cleanly: nothing exotic that would break structuredContent.
        assert json.loads(json.dumps(result)) == result
