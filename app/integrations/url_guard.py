"""URL safety guard for ExternalActionAgent outbound calls (SSRF protection).

An External Action URL is author-supplied and template-resolved, which in an
agentic system means it can ultimately derive from a language model reading
untrusted input. The platform's own backend network (OPA, Weaviate, MinIO,
Presidio, the snippet runner) and any cloud metadata endpoint must not be
reachable through it. Two rules, enforced at call time:

1.  **Scheme.** ``https`` only, unless ``EXTERNAL_ACTION_ALLOW_HTTP=true``
    for a deployment whose legitimate targets are plain-http.
2.  **Resolution.** Every address the hostname resolves to must be publicly
    routable. Private (RFC 1918 / ULA), loopback, link-local (the cloud
    metadata range), reserved, multicast, and unspecified addresses are
    refused — for IP literals as well as DNS names.

The resolver is injectable so tests stay hermetic; production uses
``socket.getaddrinfo``.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlsplit


class ExternalActionUrlError(ValueError):
    """A candidate External Action URL cannot be used safely."""


def _is_forbidden_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_external_action_url(
    url: str,
    *,
    allow_http: bool = False,
    resolve: Callable[..., list] = socket.getaddrinfo,
) -> str:
    """Validate an outbound External Action URL and return it unchanged.

    Args:
        url: Candidate URL from node config (after template resolution).
        allow_http (bool): Permit plain-http targets (deployment escape
            hatch); https is always permitted.
        resolve: DNS resolver, injectable for tests.

    Returns:
        str: The validated URL.

    Raises:
        ExternalActionUrlError: When the scheme, credentials, hostname, or
            any resolved address is forbidden.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise ExternalActionUrlError(
            "External Action has no URL to validate."
        )
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise ExternalActionUrlError(
            f"External Action URL is malformed: {exc}"
        ) from exc

    scheme = parts.scheme.lower()
    if scheme == "http":
        if not allow_http:
            raise ExternalActionUrlError(
                "External Action URLs must use https (set "
                "EXTERNAL_ACTION_ALLOW_HTTP=true to permit plain http)."
            )
    elif scheme != "https":
        raise ExternalActionUrlError(
            f"External Action URL scheme {scheme!r} is not permitted; "
            "only https (and optionally http) are."
        )

    if parts.username is not None or parts.password is not None:
        raise ExternalActionUrlError(
            "External Action URLs must not embed credentials; send them "
            "as headers instead."
        )

    host = parts.hostname
    if not host:
        raise ExternalActionUrlError(
            "External Action URL has no hostname."
        )

    # IP literal: judge it directly, no DNS involved.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_forbidden_address(literal):
            raise ExternalActionUrlError(
                f"External Action target {host} is a forbidden address "
                "(private, loopback, link-local, or reserved)."
            )
        return candidate

    try:
        infos = resolve(host, None)
    except (socket.gaierror, OSError) as exc:
        raise ExternalActionUrlError(
            f"External Action target {host!r} cannot be resolved: {exc}"
        ) from exc

    addresses = {info[4][0] for info in infos if info and info[4]}
    if not addresses:
        raise ExternalActionUrlError(
            f"External Action target {host!r} resolved to no addresses."
        )
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise ExternalActionUrlError(
                f"External Action target {host!r} resolved to an "
                f"unparseable address {raw!r}."
            )
        if _is_forbidden_address(ip):
            raise ExternalActionUrlError(
                f"External Action target {host!r} resolves to a forbidden "
                f"address ({raw}); internal and metadata endpoints are "
                "not reachable through External Actions."
            )
    return candidate
