"""The email capability.

Two design claims are under test. First, that one node with an operation selector
genuinely replaces a node type per provider and verb — so the same node config
shape works against two different adapters. Second, and more important, that the
safety rules an author should not have to think about are enforced *below* the
node: sending needs permission, a retried send does not duplicate, and a send
that failed ambiguously is never quietly retried.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.integrations.email import (
    EmailAddress,
    EmailAmbiguousFailure,
    EmailConnection,
    EmailConnectionError,
    EmailDraft,
    EmailMessage,
    EmailOperationInFlight,
    EmailSearchCriteria,
    EmailService,
    GmailAdapter,
    InMemoryEmailAdapter,
    MicrosoftGraphAdapter,
    idempotency_key,
    load_connections,
)
from app.nodes.email_integration import EmailAgent, is_side_effect


def message(**overrides) -> EmailMessage:
    defaults = dict(
        id="m1",
        subject="Pumpe ausgefallen",
        body_text="Unsere Dura 15 Pumpe ist ausgefallen.",
        from_address=EmailAddress(email="kunde@werke.de", name="H. Meier"),
        received_at=datetime.now(UTC),
        is_unread=True,
    )
    defaults.update(overrides)
    return EmailMessage(**defaults)


@pytest.fixture
def adapter() -> InMemoryEmailAdapter:
    return InMemoryEmailAdapter([message()])


@pytest.fixture
def service(adapter: InMemoryEmailAdapter) -> EmailService:
    return EmailService(
        adapters={"memory": adapter},
        connections={
            "read_only": EmailConnection(
                id="read_only",
                provider="memory",
                address="support@eurskem.com",
                allow_send=False,
            ),
            "sending": EmailConnection(
                id="sending",
                provider="memory",
                address="support@eurskem.com",
                allow_send=True,
            ),
        },
    )


def node(config: dict, service: EmailService) -> EmailAgent:
    instance = EmailAgent("email_step", config)
    instance.services = {"email": service}
    return instance


async def run(instance: EmailAgent, run_id: str = "run-1") -> dict:
    return await instance.run(
        {"inputs": {"SYSTEM.run_id": run_id}}, instance.config.model_dump()
    )


class TestOperations:
    @pytest.mark.asyncio
    async def test_search_returns_normalised_messages(self):
        adapter = InMemoryEmailAdapter([message()])
        service = EmailService(
            adapters={"memory": adapter},
            connections={
                "read_only": EmailConnection(id="read_only", provider="memory")
            },
        )
        output = await run(
            node({"connection": "read_only", "operation": "search", "unread_only": True}, service)
        )
        assert output["message_count"] == 1
        assert output["messages"][0]["from_email"] == "kunde@werke.de"
        assert output["messages"][0]["subject"] == "Pumpe ausgefallen"

    @pytest.mark.asyncio
    async def test_read_returns_one_message(self, service):
        output = await run(
            node(
                {"connection": "read_only", "operation": "read", "message_id": "m1"},
                service,
            )
        )
        assert output["message"]["id"] == "m1"

    @pytest.mark.asyncio
    async def test_create_draft_returns_a_draft_id(self, service, adapter):
        output = await run(
            node(
                {
                    "connection": "sending",
                    "operation": "create_draft",
                    "to": [{"email": "kunde@werke.de"}],
                    "subject": "Re: Pumpe",
                    "body": "Wir melden uns.",
                },
                service,
            )
        )
        assert output["draft_id"]
        assert output["sent_message_id"] is None
        assert adapter.sent == []

    @pytest.mark.asyncio
    async def test_the_output_shape_does_not_change_with_the_operation(self, service):
        """One output contract for all five operations, so switching the
        operation selector cannot break a downstream mapping's field names."""
        searched = await run(
            node({"connection": "read_only", "operation": "search"}, service)
        )
        drafted = await run(
            node(
                {
                    "connection": "sending",
                    "operation": "create_draft",
                    "to": [{"email": "a@b.c"}],
                    "subject": "s",
                    "body": "b",
                },
                service,
            )
        )
        assert set(searched) == set(drafted)


class TestPermissions:
    @pytest.mark.asyncio
    async def test_sending_from_a_read_only_connection_is_refused(self, service):
        """Deployment decides which mailboxes may send. A workflow cannot grant
        itself that permission by changing a dropdown."""
        with pytest.raises(EmailConnectionError, match="not permitted to send"):
            await run(
                node(
                    {
                        "connection": "read_only",
                        "operation": "send",
                        "to": [{"email": "a@b.c"}],
                        "subject": "s",
                        "body": "b",
                        "allow_unattended_write": True,
                    },
                    service,
                )
            )

    @pytest.mark.asyncio
    async def test_an_unknown_connection_names_what_is_available(self, service):
        with pytest.raises(EmailConnectionError, match="Available"):
            await run(
                node({"connection": "nonexistent", "operation": "search"}, service)
            )

    def test_connection_descriptions_never_include_credentials(self, service):
        service.connections["sending"].credentials["access_token"] = "secret-token"
        described = service.describe_connections()
        assert "secret-token" not in str(described)
        assert {item["id"] for item in described} == {"read_only", "sending"}


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_a_retried_run_does_not_send_twice(self, service, adapter):
        """The case this exists for: a run is retried after a failure downstream
        of the send, and the customer must not receive the reply twice."""
        config = {
            "connection": "sending",
            "operation": "send",
            "to": [{"email": "kunde@werke.de"}],
            "subject": "Re: Pumpe",
            "body": "Wir melden uns.",
            "allow_unattended_write": True,
        }
        first = await run(node(config, service), run_id="run-1")
        second = await run(node(config, service), run_id="run-1")

        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert second["sent_message_id"] == first["sent_message_id"]
        assert len(adapter.sent) == 1

    @pytest.mark.asyncio
    async def test_two_different_runs_both_send(self, service, adapter):
        """Two customers getting the same templated reply are not duplicates."""
        config = {
            "connection": "sending",
            "operation": "send",
            "to": [{"email": "kunde@werke.de"}],
            "subject": "Re: Pumpe",
            "body": "Wir melden uns.",
            "allow_unattended_write": True,
        }
        await run(node(config, service), run_id="run-1")
        await run(node(config, service), run_id="run-2")
        assert len(adapter.sent) == 2

    @pytest.mark.asyncio
    async def test_a_definitive_failure_frees_the_key_for_a_corrected_retry(
        self, service
    ):
        """A rejected address is not a "maybe sent" — after fixing it, the retry
        must not be refused as a duplicate."""

        class RejectingAdapter(InMemoryEmailAdapter):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            async def send(self, connection, draft):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("recipient address rejected")
                return await super().send(connection, draft)

        rejecting = RejectingAdapter()
        service.adapters["memory"] = rejecting
        config = {
            "connection": "sending",
            "operation": "send",
            "to": [{"email": "kunde@werke.de"}],
            "subject": "s",
            "body": "b",
            "allow_unattended_write": True,
        }
        with pytest.raises(RuntimeError, match="rejected"):
            await run(node(config, service), run_id="run-1")

        recovered = await run(node(config, service), run_id="run-1")
        assert recovered["sent_message_id"]
        assert recovered["deduplicated"] is False

    @pytest.mark.asyncio
    async def test_an_ambiguous_failure_blocks_the_retry_and_says_why(self, service):
        """The §49 case. A timeout on a send may mean the message went out, so
        the platform refuses to guess and tells a person what to check."""

        class TimingOutAdapter(InMemoryEmailAdapter):
            async def send(self, connection, draft):
                raise EmailAmbiguousFailure("gateway timed out after accepting")

        service.adapters["memory"] = TimingOutAdapter()
        config = {
            "connection": "sending",
            "operation": "send",
            "to": [{"email": "kunde@werke.de"}],
            "subject": "s",
            "body": "b",
            "allow_unattended_write": True,
        }
        with pytest.raises(EmailAmbiguousFailure):
            await run(node(config, service), run_id="run-1")

        with pytest.raises(EmailOperationInFlight, match="check the mailbox"):
            await run(node(config, service), run_id="run-1")

    def test_the_key_ignores_recipient_ordering(self):
        base = dict(connection_id="c", operation="send", scope="run-1")
        one = idempotency_key(
            draft=EmailDraft(
                to=[EmailAddress(email="a@x.com"), EmailAddress(email="b@x.com")],
                subject="s",
                body_text="b",
            ),
            **base,
        )
        other = idempotency_key(
            draft=EmailDraft(
                to=[EmailAddress(email="B@x.com"), EmailAddress(email="a@x.com")],
                subject="s",
                body_text="b",
            ),
            **base,
        )
        assert one == other

    def test_a_different_body_is_a_different_operation(self):
        base = dict(connection_id="c", operation="send", scope="run-1")
        one = idempotency_key(
            draft=EmailDraft(to=[EmailAddress(email="a@x.com")], body_text="first"),
            **base,
        )
        other = idempotency_key(
            draft=EmailDraft(to=[EmailAddress(email="a@x.com")], body_text="second"),
            **base,
        )
        assert one != other


class TestSideEffectClassification:
    def test_reads_are_not_side_effects(self):
        assert is_side_effect("search") is False
        assert is_side_effect("read") is False

    def test_writes_are_side_effects(self):
        assert is_side_effect("send") is True
        assert is_side_effect("reply") is True
        assert is_side_effect("create_draft") is True


class TestProviderAdapters:
    """Provider differences belong below the node contract. These tests drive
    the real adapters through a mocked transport, so the translation is verified
    rather than assumed."""

    @pytest.mark.asyncio
    async def test_gmail_search_translates_criteria_into_the_q_grammar(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/messages"):
                seen["query"] = dict(request.url.params)
                return httpx.Response(200, json={"messages": [{"id": "abc"}]})
            return httpx.Response(
                200,
                json={
                    "id": "abc",
                    "threadId": "t1",
                    "labelIds": ["UNREAD"],
                    "internalDate": "1700000000000",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Pumpe"},
                            {"name": "From", "value": "H. Meier <kunde@werke.de>"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": "SGFsbG8="},
                    },
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GmailAdapter(client=client)
        connection = EmailConnection(
            id="g", provider="gmail", credentials={"access_token": "t"}
        )
        found = await adapter.search(
            connection,
            EmailSearchCriteria(
                query="Pumpe", unread_only=True, newer_than_days=7, max_results=5
            ),
        )
        await client.aclose()

        assert "is:unread" in seen["query"]["q"]
        assert "newer_than:7d" in seen["query"]["q"]
        assert found[0].subject == "Pumpe"
        assert found[0].from_address.email == "kunde@werke.de"
        assert found[0].body_text == "Hallo"
        assert found[0].is_unread is True

    @pytest.mark.asyncio
    async def test_gmail_finds_a_body_nested_in_multipart_parts(self):
        """Gmail nests parts arbitrarily once attachments are involved; a
        two-part assumption silently produces empty bodies."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "abc",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": [],
                        "parts": [
                            {
                                "mimeType": "multipart/alternative",
                                "parts": [
                                    {
                                        "mimeType": "text/plain",
                                        "body": {"data": "VGllZg=="},
                                    }
                                ],
                            },
                            {
                                "mimeType": "application/pdf",
                                "filename": "spec.pdf",
                                "body": {"attachmentId": "att1", "size": 1234},
                            },
                        ],
                    },
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GmailAdapter(client=client)
        found = await adapter.read(
            EmailConnection(id="g", provider="gmail", credentials={"access_token": "t"}),
            "abc",
        )
        await client.aclose()

        assert found.body_text == "Tief"
        assert found.attachments[0].name == "spec.pdf"
        assert found.attachments[0].size_bytes == 1234

    @pytest.mark.asyncio
    async def test_gmail_timeout_on_send_is_ambiguous_not_a_plain_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GmailAdapter(client=client)
        with pytest.raises(EmailAmbiguousFailure):
            await adapter.send(
                EmailConnection(
                    id="g", provider="gmail", credentials={"access_token": "t"}
                ),
                EmailDraft(to=[EmailAddress(email="a@b.c")], subject="s", body_text="b"),
            )
        await client.aclose()

    @pytest.mark.asyncio
    async def test_gmail_timeout_on_search_is_a_plain_failure(self):
        """A read changes nothing, so an ambiguous outcome does not exist for it —
        it is simply a retryable failure."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = GmailAdapter(client=client)
        with pytest.raises(Exception) as caught:
            await adapter.search(
                EmailConnection(
                    id="g", provider="gmail", credentials={"access_token": "t"}
                ),
                EmailSearchCriteria(),
            )
        await client.aclose()
        assert not isinstance(caught.value, EmailAmbiguousFailure)

    @pytest.mark.asyncio
    async def test_graph_search_translates_criteria_into_odata_filters(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "AAMk",
                            "conversationId": "conv1",
                            "subject": "Pump failure",
                            "isRead": False,
                            "body": {"contentType": "text", "content": "Hello"},
                            "from": {
                                "emailAddress": {
                                    "address": "kunde@werke.de",
                                    "name": "H. Meier",
                                }
                            },
                            "receivedDateTime": "2026-08-01T10:00:00Z",
                        }
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = MicrosoftGraphAdapter(client=client)
        found = await adapter.search(
            EmailConnection(id="m", provider="microsoft", credentials={"access_token": "t"}),
            EmailSearchCriteria(unread_only=True, has_attachments=True),
        )
        await client.aclose()

        assert "isRead eq false" in seen["params"]["$filter"]
        assert "hasAttachments eq true" in seen["params"]["$filter"]
        assert found[0].thread_id == "conv1"
        assert found[0].is_unread is True
        assert found[0].from_address.name == "H. Meier"

    @pytest.mark.asyncio
    async def test_graph_reply_uses_the_provider_reply_endpoint(self):
        """Graph threads and quotes server-side, which a generic send cannot do."""
        called: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            called.append(request.url.path)
            return httpx.Response(202)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = MicrosoftGraphAdapter(client=client)
        await adapter.reply(
            EmailConnection(id="m", provider="microsoft", credentials={"access_token": "t"}),
            EmailDraft(
                to=[EmailAddress(email="kunde@werke.de")],
                body_text="Wir melden uns.",
                in_reply_to_message_id="AAMk",
            ),
        )
        await client.aclose()
        assert called == ["/v1.0/me/messages/AAMk/reply"]

    @pytest.mark.asyncio
    async def test_a_missing_token_is_an_actionable_error(self):
        adapter = GmailAdapter()
        with pytest.raises(Exception, match="secret store"):
            await adapter.search(
                EmailConnection(id="g", provider="gmail"), EmailSearchCriteria()
            )

    @pytest.mark.asyncio
    async def test_the_same_node_config_works_against_both_providers(self):
        """The point of the whole abstraction: switching provider is a
        connection change, not a node change."""

        def gmail_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, json={"messages": []})
            return httpx.Response(200, json={})

        def graph_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"value": []})

        gmail_client = httpx.AsyncClient(transport=httpx.MockTransport(gmail_handler))
        graph_client = httpx.AsyncClient(transport=httpx.MockTransport(graph_handler))
        service = EmailService(
            adapters={
                "gmail": GmailAdapter(client=gmail_client),
                "microsoft": MicrosoftGraphAdapter(client=graph_client),
            },
            connections={
                "g": EmailConnection(
                    id="g", provider="gmail", credentials={"access_token": "t"}
                ),
                "m": EmailConnection(
                    id="m", provider="microsoft", credentials={"access_token": "t"}
                ),
            },
        )
        config = {"operation": "search", "unread_only": True}
        via_gmail = await run(node({**config, "connection": "g"}, service))
        via_graph = await run(node({**config, "connection": "m"}, service))
        await gmail_client.aclose()
        await graph_client.aclose()

        assert via_gmail["provider"] == "gmail"
        assert via_graph["provider"] == "microsoft"
        assert set(via_gmail) == set(via_graph)


class TestSearchCriteriaSemantics:
    @pytest.mark.asyncio
    async def test_in_memory_adapter_honours_every_criterion(self):
        adapter = InMemoryEmailAdapter(
            [
                message(id="old", received_at=datetime.now(UTC) - timedelta(days=90)),
                message(id="read", is_unread=False),
                message(id="fresh"),
            ]
        )
        connection = EmailConnection(id="c", provider="memory")
        found = await adapter.search(
            connection, EmailSearchCriteria(unread_only=True, newer_than_days=30)
        )
        assert [item.id for item in found] == ["fresh"]

    @pytest.mark.asyncio
    async def test_results_are_capped_by_max_results(self):
        adapter = InMemoryEmailAdapter(
            [message(id=f"m{index}") for index in range(20)]
        )
        found = await adapter.search(
            EmailConnection(id="c", provider="memory"),
            EmailSearchCriteria(max_results=5),
        )
        assert len(found) == 5


class TestConnectionLoading:
    def test_tokens_are_resolved_from_the_environment_by_name(self, monkeypatch):
        """The connection definition holds a variable *name*, so it can live in
        non-secret configuration and never carries the token itself."""
        monkeypatch.setenv("SUPPORT_TOKEN", "real-token")
        connections = load_connections(
            '[{"id":"support","provider":"microsoft",'
            '"credentials":{"access_token_env":"SUPPORT_TOKEN"}}]'
        )
        assert connections["support"].credentials["access_token"] == "real-token"

    def test_one_broken_connection_does_not_discard_the_others(self):
        connections = load_connections(
            '[{"id":"good","provider":"memory"},{"id":"bad","provider":"nope"}]'
        )
        assert set(connections) == {"good"}

    def test_unparseable_configuration_yields_no_connections_rather_than_raising(self):
        """A malformed mailbox definition must not stop the API from starting;
        preflight reports the missing connection against the workflow needing it."""
        assert load_connections("not json") == {}

    def test_no_configuration_is_not_an_error(self):
        assert load_connections("") == {}
