"""SSRF guard for ExternalActionAgent outbound calls.

The safety claim: a template-resolved URL — which in an agentic system can
ultimately derive from a model reading untrusted input — must never reach the
platform's own backend network or a cloud metadata endpoint. These tests use
an injected fake resolver so they are hermetic (no real DNS), covering IP
literals, DNS names resolving to forbidden ranges, scheme policy, and
credential rejection.
"""
from __future__ import annotations

import socket

import httpx
import pytest

from app.integrations.external_action import (
    ExternalActionError,
    ExternalActionService,
)
from app.integrations.url_guard import (
    ExternalActionUrlError,
    validate_external_action_url,
)


def _resolve_to(*addresses: str):
    """A fake getaddrinfo returning the given addresses for any host."""

    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))
                for addr in addresses]

    return resolve


def _resolve_gaierror(host, port, *args, **kwargs):
    raise socket.gaierror("name resolution failed")


# ---- scheme policy ---------------------------------------------------------

def test_https_is_allowed_with_a_public_resolution():
    url = validate_external_action_url(
        "https://api.example.com/orders", resolve=_resolve_to("93.184.216.34")
    )
    assert url == "https://api.example.com/orders"


def test_plain_http_is_refused_by_default():
    with pytest.raises(ExternalActionUrlError, match="https"):
        validate_external_action_url(
            "http://api.example.com/orders",
            resolve=_resolve_to("93.184.216.34"),
        )


def test_plain_http_is_allowed_when_the_escape_hatch_is_set():
    url = validate_external_action_url(
        "http://intranet-webhook.example/orders",
        allow_http=True,
        resolve=_resolve_to("93.184.216.34"),
    )
    assert url.startswith("http://")


def test_non_http_schemes_are_refused():
    for scheme in ("file", "ftp", "gopher", "javascript"):
        with pytest.raises(ExternalActionUrlError, match="scheme"):
            validate_external_action_url(f"{scheme}://example.com/x")


# ---- forbidden address ranges ---------------------------------------------

@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",            # loopback
        "10.0.0.5",             # RFC1918
        "172.16.3.4",           # RFC1918
        "192.168.1.10",         # RFC1918
        "169.254.169.254",      # cloud metadata / link-local
        "0.0.0.0",              # unspecified
        "224.0.0.1",            # multicast
    ],
)
def test_ip_literals_in_forbidden_ranges_are_refused(address):
    with pytest.raises(ExternalActionUrlError, match="forbidden"):
        validate_external_action_url(f"https://{address}/hook")


def test_a_dns_name_resolving_to_a_private_address_is_refused():
    with pytest.raises(ExternalActionUrlError, match="forbidden"):
        validate_external_action_url(
            "https://weaviate.internal:8080/v1",
            resolve=_resolve_to("172.20.0.5"),
        )


def test_a_dns_name_resolving_to_the_metadata_endpoint_is_refused():
    with pytest.raises(ExternalActionUrlError, match="forbidden"):
        validate_external_action_url(
            "https://metadata.host/latest/api-token",
            resolve=_resolve_to("169.254.169.254"),
        )


def test_any_forbidden_address_among_several_refuses_the_call():
    with pytest.raises(ExternalActionUrlError, match="forbidden"):
        validate_external_action_url(
            "https://dual.example.com/",
            resolve=_resolve_to("93.184.216.34", "10.0.0.1"),
        )


def test_unresolvable_hosts_are_refused_not_silently_passed():
    with pytest.raises(ExternalActionUrlError, match="cannot be resolved"):
        validate_external_action_url(
            "https://no-such-host.invalid/", resolve=_resolve_gaierror
        )


# ---- credentials and shape -------------------------------------------------

def test_embedded_credentials_are_refused():
    with pytest.raises(ExternalActionUrlError, match="credentials"):
        validate_external_action_url(
            "https://user:pass@api.example.com/orders",
            resolve=_resolve_to("93.184.216.34"),
        )


def test_a_missing_hostname_is_refused():
    with pytest.raises(ExternalActionUrlError):
        validate_external_action_url("https:///no-host")


def test_an_empty_url_is_refused():
    with pytest.raises(ExternalActionUrlError, match="no URL"):
        validate_external_action_url("   ")


# ---- service-level enforcement --------------------------------------------

@pytest.mark.asyncio
async def test_the_service_refuses_a_forbidden_target_before_any_call():
    """The guard fires before the transport; the handler must never run."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a forbidden target must never reach the wire")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = ExternalActionService(http_client=client)

    with pytest.raises(ExternalActionError) as caught:
        await service.call(
            run_id="run-1",
            node_id="call_api",
            safety_class="read",
            method="GET",
            url="https://169.254.169.254/latest/meta-data/",
            headers={},
            body=None,
            timeout_seconds=5,
            approval_satisfied=True,
        )
    assert caught.value.code == "EXTERNAL_ACTION_URL_FORBIDDEN"
