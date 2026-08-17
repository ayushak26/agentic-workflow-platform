"""ExternalActionService — the one place an outbound REST/webhook call goes through.

Kept structurally parallel to `MCPService.call`/`EmailService.execute`: a
write or external-action call is reserved in the shared idempotency ledger
*before* the request goes out, so a timeout or a dropped connection leaves a
durable "this may have happened" record instead of a silence the next retry
would turn into a duplicate order in someone else's system. Read calls are
never reserved — there is nothing to deduplicate.

Always constructs, even with nothing configured — there is no credential to
be missing, since the author supplies the URL. It only raises when actually
called with a request the policy refuses.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.integrations.operations import (
    AmbiguousOperationFailure,
    ExternalOperationLedger,
    OperationInFlight,
    operation_key,
)
from app.observability.logging import get_logger

log = get_logger(__name__)

_WRITE_CLASSES = {"write", "external_action"}


class ExternalActionError(RuntimeError):
    """A call failed in a way worth reporting to a workflow author."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "EXTERNAL_ACTION_ERROR",
        retryable: bool = False,
    ):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class ExternalActionService:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        ledger: ExternalOperationLedger | None = None,
        db: Any = None,
    ) -> None:
        # Injected only by tests, against a real httpx.MockTransport — in
        # production a fresh client is opened per call so each call's own
        # timeout is the one actually enforced.
        self._http_client = http_client
        self.ledger = ledger or ExternalOperationLedger(db, collection="external_actions")

    async def call(
        self,
        *,
        run_id: str,
        node_id: str,
        safety_class: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any,
        timeout_seconds: float,
        approval_satisfied: bool,
    ) -> dict[str, Any]:
        url = (url or "").strip()
        if not url:
            raise ExternalActionError(
                "External Action has no URL to call — check the mapping that "
                "resolves it.",
                code="EXTERNAL_ACTION_NO_URL",
            )

        writing = safety_class in _WRITE_CLASSES
        if writing and not approval_satisfied:
            raise ExternalActionError(
                f"{safety_class} action refused: no human review has run on "
                "this run's path, and allow_unattended_write is not set.",
                code="EXTERNAL_ACTION_APPROVAL_REQUIRED",
            )

        key = ""
        if writing:
            # Reserved before the call, exactly like MCP's write path and
            # Email's send path — the same problem wearing a third outfit.
            key = operation_key(
                scope=f"{run_id}:{node_id}",
                target=f"{method}:{url}",
                payload=body,
            )
            existing = await self.ledger.reserve(
                key,
                {
                    "status": "in_flight",
                    "safety_class": safety_class,
                    "method": method,
                    "url": url,
                    "run_id": run_id,
                    "node_id": node_id,
                },
            )
            if existing is not None:
                return self._replay_or_refuse(existing, key)

        started = time.time()
        client = self._http_client
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient()
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await client.request(
                    method,
                    url,
                    headers=headers or None,
                    json=body if body is not None else None,
                    timeout=timeout_seconds,
                )
        except (TimeoutError, httpx.TimeoutException) as error:
            if writing:
                await self.ledger.mark_ambiguous(key, str(error))
                raise AmbiguousOperationFailure(
                    f"{method} {url} timed out after {timeout_seconds}s. It may "
                    f"have taken effect on the other side. Check the target "
                    f"system before retrying (operation key {key[:12]})."
                ) from error
            raise ExternalActionError(
                f"{method} {url} timed out after {timeout_seconds}s.",
                code="EXTERNAL_ACTION_TIMEOUT",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            # A connection that never reached the target did nothing — the
            # reservation is released so a corrected retry is not refused as a
            # duplicate of a call that never happened.
            if writing:
                await self.ledger.release(key, str(error))
            raise ExternalActionError(
                f"{method} {url} failed: {error}",
                code="EXTERNAL_ACTION_TRANSPORT_ERROR",
                retryable=True,
            ) from error
        finally:
            if owns_client:
                await client.aclose()

        duration = time.time() - started
        response_body = _parse_body(response)

        if writing:
            if response.status_code >= 500:
                # The target accepted the request but failed server-side —
                # genuinely ambiguous, same as MCP/email's own 5xx handling.
                await self.ledger.mark_ambiguous(
                    key, f"HTTP {response.status_code}"
                )
                raise AmbiguousOperationFailure(
                    f"{method} {url} returned HTTP {response.status_code}. It "
                    "may have partially applied. Check the target system "
                    f"before retrying (operation key {key[:12]})."
                )
            if response.status_code >= 400:
                # Rejected outright (bad request, auth) — nothing happened.
                await self.ledger.release(key, f"HTTP {response.status_code}")
            else:
                await self.ledger.complete(
                    key,
                    {
                        "response_status": response.status_code,
                        "response_body": response_body,
                    },
                )

        return {
            "response_status": response.status_code,
            "response_body": response_body,
            "duration_s": duration,
            "deduplicated": False,
        }

    def _replay_or_refuse(self, existing: dict[str, Any], key: str) -> dict[str, Any]:
        status = existing.get("status")
        if status == "completed":
            return {
                "response_status": existing.get("response_status"),
                "response_body": existing.get("response_body"),
                "duration_s": 0.0,
                "deduplicated": True,
            }
        if status == "ambiguous":
            raise OperationInFlight(
                "A previous attempt at this exact action ended ambiguously "
                f"(operation key {key[:12]}). A person must check the target "
                "system before this can run again."
            )
        raise OperationInFlight(
            f"An identical action is already in flight (operation key {key[:12]})."
        )


def _parse_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return response.json()
        except ValueError:
            pass
    # Bounded so a misbehaving endpoint's response can't bloat run state —
    # the same ceiling MCPToolError's own message truncation uses elsewhere.
    return response.text[:4000]


_default_service: ExternalActionService | None = None


def get_external_action_service() -> ExternalActionService:
    global _default_service
    if _default_service is None:
        _default_service = ExternalActionService()
    return _default_service
