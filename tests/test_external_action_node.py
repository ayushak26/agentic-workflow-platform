"""ExternalActionAgent — the generic outward-facing REST/webhook node.

Kept apart from MCP by design (see app/nodes/external_action.py's module
docstring), but the safety claim under test is the same one MCP and Email
already have to prove: a write must not run unattended without a stated
reason, a retry must not repeat a call whose outcome is already known, and an
ambiguous failure (a timeout, a 5xx) must leave a durable record rather than
being silently retried.
"""
from __future__ import annotations

import httpx
import pytest

from app.integrations.external_action import ExternalActionService
from app.integrations.operations import AmbiguousOperationFailure, OperationInFlight
from app.nodes.external_action import ExternalActionAgent


def node(config: dict, service: ExternalActionService, node_id: str = "call_api") -> ExternalActionAgent:
    instance = ExternalActionAgent(node_id, config)
    instance.services = {"external_action": service}
    return instance


async def run(instance: ExternalActionAgent, *, run_id: str = "run-1", state=None):
    base = {"inputs": {"SYSTEM.run_id": run_id}, "node_outputs": {}}
    base.update(state or {})
    return await instance.run(base, instance.config.model_dump())


def service(handler) -> ExternalActionService:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ExternalActionService(http_client=client)


READ_CONFIG = {
    "action_type": "rest_api",
    "safety_class": "read",
    "method": "GET",
    "url": "https://api.example.com/orders/42",
}

WRITE_CONFIG = {
    "action_type": "rest_api",
    "safety_class": "write",
    "method": "POST",
    "url": "https://api.example.com/orders",
    "body": {"customer": "Acme"},
}


class TestReadAndWebhookCalls:
    @pytest.mark.asyncio
    async def test_a_read_call_succeeds_with_no_approval_needed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json={"order_id": 42, "status": "shipped"})

        instance = node(READ_CONFIG, service(handler))
        result = await run(instance)
        assert result["status"] == "ok"
        assert result["response_status"] == 200
        assert result["response_body"] == {"order_id": 42, "status": "shipped"}
        assert result["safety_class"] == "read"

    @pytest.mark.asyncio
    async def test_a_webhook_posts_with_the_configured_body(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return httpx.Response(202, text="accepted")

        config = {
            "action_type": "webhook",
            "safety_class": "external_action",
            "url": "https://hooks.example.com/notify",
            "body": {"event": "order_created"},
            "allow_unattended_write": True,
        }
        instance = node(config, service(handler))
        result = await run(instance)
        assert result["status"] == "ok"
        assert result["response_status"] == 202
        assert b"order_created" in seen["body"]

    @pytest.mark.asyncio
    async def test_a_non_json_response_is_captured_as_bounded_text(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="plain text body")

        instance = node(READ_CONFIG, service(handler))
        result = await run(instance)
        assert result["response_body"] == "plain text body"


class TestApprovalGating:
    @pytest.mark.asyncio
    async def test_a_write_with_no_approval_and_no_override_is_refused(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not call out when approval is missing")

        instance = node(WRITE_CONFIG, service(handler))
        result = await run(instance)
        assert result["status"] == "needs_approval"
        assert result["error_code"] == "EXTERNAL_ACTION_APPROVAL_REQUIRED"

    @pytest.mark.asyncio
    async def test_allow_unattended_write_lets_it_run(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"id": "ord_1"})

        config = {**WRITE_CONFIG, "allow_unattended_write": True}
        instance = node(config, service(handler))
        result = await run(instance)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_a_prior_human_approval_on_the_run_path_lets_it_run(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"id": "ord_2"})

        instance = node(WRITE_CONFIG, service(handler))
        state = {"node_outputs": {"review": {"decision": "approve"}}}
        result = await run(instance, state=state)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_a_rejection_does_not_count_as_approval(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not call out on a rejected review")

        instance = node(WRITE_CONFIG, service(handler))
        state = {"node_outputs": {"review": {"decision": "reject"}}}
        result = await run(instance, state=state)
        assert result["status"] == "needs_approval"


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_a_repeated_write_replays_the_recorded_result_instead_of_calling_again(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(201, json={"id": "ord_3"})

        svc = service(handler)
        config = {**WRITE_CONFIG, "allow_unattended_write": True}
        first = await run(node(config, svc))
        second = await run(node(config, svc))

        assert len(calls) == 1
        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert second["response_body"] == {"id": "ord_3"}

    @pytest.mark.asyncio
    async def test_an_in_flight_reservation_refuses_a_concurrent_duplicate(self):
        svc = service(lambda request: httpx.Response(201, json={}))
        config = {**WRITE_CONFIG, "allow_unattended_write": True}
        key_payload = WRITE_CONFIG["body"]
        from app.integrations.operations import operation_key

        key = operation_key(
            scope="run-1:call_api",
            target=f"{WRITE_CONFIG['method']}:{WRITE_CONFIG['url']}",
            payload=key_payload,
        )
        await svc.ledger.reserve(key, {"status": "in_flight"})

        result = await run(node(config, svc))
        assert result["status"] == "error"
        assert "in flight" in result["error"]

    @pytest.mark.asyncio
    async def test_two_different_runs_performing_the_same_write_do_not_collide(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(201, json={"id": "ord_4"})

        svc = service(handler)
        config = {**WRITE_CONFIG, "allow_unattended_write": True}
        await run(node(config, svc), run_id="run-A")
        await run(node(config, svc), run_id="run-B")
        assert len(calls) == 2


class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_a_read_timeout_is_a_plain_error_not_an_ambiguous_one(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("took too long")

        instance = node(READ_CONFIG, service(handler))
        result = await run(instance)
        assert result["status"] == "error"
        assert result["error_code"] == "EXTERNAL_ACTION_TIMEOUT"
        assert result["retryable"] is True

    @pytest.mark.asyncio
    async def test_a_write_timeout_leaves_an_ambiguous_record_not_a_plain_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("took too long")

        config = {**WRITE_CONFIG, "allow_unattended_write": True}
        svc = service(handler)
        instance = node(config, svc)
        result = await run(instance)
        assert result["status"] == "error"
        assert result["error_code"] == "AmbiguousOperationFailure"

        # The reservation must still be there — a blind retry must not
        # silently duplicate the order this call may have already placed.
        from app.integrations.operations import operation_key

        key = operation_key(
            scope="run-1:call_api",
            target=f"{WRITE_CONFIG['method']}:{WRITE_CONFIG['url']}",
            payload=WRITE_CONFIG["body"],
        )
        record = await svc.ledger.find(key)
        assert record["status"] == "ambiguous"

    @pytest.mark.asyncio
    async def test_a_write_5xx_is_ambiguous_a_4xx_releases_the_reservation(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/a":
                return httpx.Response(500)
            # /b: 400 on every call — proves the released reservation lets a
            # retry actually reach the target again, not just replay a record.
            return httpx.Response(400)

        svc = service(handler)
        config_500 = {**WRITE_CONFIG, "allow_unattended_write": True, "url": "https://api.example.com/a"}
        config_400 = {**WRITE_CONFIG, "allow_unattended_write": True, "url": "https://api.example.com/b"}

        result_500 = await run(node(config_500, svc))
        assert result_500["error_code"] == "AmbiguousOperationFailure"

        result_400 = await run(node(config_400, svc, node_id="call_b"))
        assert result_400["status"] == "ok"
        assert result_400["response_status"] == 400
        # A 4xx did nothing on the other side, so retrying it must not be
        # refused as a duplicate.
        second = await run(node(config_400, svc, node_id="call_b"))
        assert second["response_status"] == 400
        assert second["deduplicated"] is False

    @pytest.mark.asyncio
    async def test_a_missing_url_is_a_clear_config_error(self):
        config = {**READ_CONFIG, "url": ""}
        instance = ExternalActionAgent("call_api", {**config})
        instance.services = {"external_action": service(lambda r: httpx.Response(200))}
        result = await run(instance)
        assert result["error_code"] == "EXTERNAL_ACTION_NO_URL"
