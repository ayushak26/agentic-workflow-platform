"""Email OAuth connect flow — Outlook (Microsoft Graph) and Gmail.

Mounted alongside the existing /email/* routes in app/api/builder.py (same
/api/builder prefix, same auth dependency). This is the one piece
app/integrations/email/ was missing: everything else (adapters, gating,
idempotency, the read-only connections list) already existed. What's here
is purely the authorization-code round trip and the connect/disconnect/
allow-send actions the Builder's "+ Connect Outlook"/"+ Connect Gmail"
buttons drive.

`state` doubles as CSRF protection and (for Gmail) carries the PKCE code
verifier across the redirect round trip — a short-lived Mongo document,
single-use (deleted the moment the callback consumes it), so an abandoned
connect attempt just leaves a small, harmless, unconsumed record rather than
anything sensitive.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from app.integrations.email import oauth
from app.integrations.email.base import EmailConnection
from app.integrations.email.connections_store import (
    delete_connection_record,
    save_connection_record,
    set_allow_send,
)
from app.integrations.email.oauth import OAuthConfigurationError, OAuthExchangeError
from app.integrations.email.token_vault import TokenVault
from app.observability.logging import get_logger
from app.security.dependencies import CurrentUser, require_consultant

log = get_logger(__name__)

router = APIRouter(prefix="/api/builder", tags=["email-oauth"])

_STATES_COLLECTION = "email_oauth_pending_states"
_STATE_TTL = timedelta(minutes=10)

Provider = Literal["microsoft", "gmail"]

_PROVIDER_DISPLAY_NAMES: dict[Provider, str] = {
    "microsoft": "Outlook",
    "gmail": "Gmail",
}


async def ensure_indexes(db: Any) -> None:
    """TTL-expires an abandoned connect attempt's pending state — a user who
    starts connecting a mailbox and never finishes leaves one small,
    non-secret document (no token, no code) that ages out on its own."""
    await db[_STATES_COLLECTION].create_index([("expires_at", 1)], expireAfterSeconds=0)


def _services(request: Request) -> dict[str, Any]:
    return getattr(request.app.state, "services", {}) or {}


def _require_db(request: Request) -> Any:
    db = _services(request).get("audit_db")
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="No database is configured in this deployment — email connections need one to persist.",
        )
    return db


def _require_email_service(request: Request) -> Any:
    service = _services(request).get("email")
    if service is None:
        raise HTTPException(status_code=503, detail="Email integration is not configured in this deployment.")
    return service


def _connection_id(provider: Provider, address: str) -> str:
    # Stable per (provider, mailbox) — reconnecting the same mailbox updates
    # its existing connection (and keeps whatever allow_send an operator
    # already set) rather than creating a duplicate.
    normalized = address.strip().lower().replace("@", "_at_").replace(".", "_")
    return f"{provider}_{normalized}" if normalized else f"{provider}_{secrets.token_hex(6)}"


def _html_result(*, ok: bool, message: str) -> HTMLResponse:
    """A minimal, self-contained page for the OAuth popup window — no
    external assets (this is a plain HTTP response, not a Builder route),
    just enough to tell the user what happened and let the opener refresh
    its connection list before the window closes."""
    color = "#0f766e" if ok else "#b42318"
    body = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{"Connected" if ok else "Connection failed"}</title></head>
<body style="font-family: system-ui, sans-serif; display: flex; align-items: center;
justify-content: center; height: 100vh; margin: 0; background: #f8fafc;">
<div style="text-align: center; color: {color};">
<p style="font-size: 15px; font-weight: 600;">{message}</p>
<p style="font-size: 12px; color: #667085;">You can close this window.</p>
</div>
<script>
try {{ if (window.opener) {{ window.opener.postMessage({{ type: "email-oauth-complete", ok: {"true" if ok else "false"} }}, "*"); }} }} catch (e) {{}}
</script>
</body></html>"""
    return HTMLResponse(content=body, status_code=200 if ok else 400)


@router.get("/email/connect/{provider}")
async def connect(
    provider: Provider,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> RedirectResponse:
    """Starts the authorization-code flow: stash a one-time state (+ PKCE
    verifier for Gmail), redirect the user's browser to the provider."""
    del user
    db = _require_db(request)

    state = secrets.token_urlsafe(32)
    code_verifier: str | None = None
    code_challenge: str | None = None
    if provider == "gmail":
        code_verifier, code_challenge = oauth.generate_pkce_pair()

    await db[_STATES_COLLECTION].insert_one({
        "_id": state,
        "provider": provider,
        "code_verifier": code_verifier,
        "created_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + _STATE_TTL,
    })

    try:
        url = oauth.authorize_url(provider, state=state, code_challenge=code_challenge)
    except OAuthConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return RedirectResponse(url, status_code=302)


@router.get("/email/oauth/callback/{provider}")
async def oauth_callback(
    provider: Provider,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    """The provider redirects the user's browser back here after consent.

    Deliberately no auth dependency — the provider's own redirect carries no
    Eurskem credential, only `code`/`state`. `state` IS the authentication
    here: it can only be one this server itself issued and has not yet
    consumed, which is exactly what CSRF protection requires.
    """
    if error:
        log.warning("email.oauth_denied", provider=provider, error=error)
        return _html_result(ok=False, message=f"{_PROVIDER_DISPLAY_NAMES[provider]} declined: {error_description or error}")
    if not code or not state:
        return _html_result(ok=False, message="Missing authorization code — try connecting again.")

    db = _require_db(request)
    pending = await db[_STATES_COLLECTION].find_one_and_delete({"_id": state})
    if pending is None:
        return _html_result(ok=False, message="This connection attempt has expired or was already used — try again.")
    if pending["provider"] != provider or datetime.now(UTC) > pending["expires_at"].replace(tzinfo=UTC):
        return _html_result(ok=False, message="This connection attempt is no longer valid — try again.")

    try:
        tokens = await oauth.exchange_code(
            provider, code=code, code_verifier=pending.get("code_verifier")
        )
        profile = await oauth.fetch_profile(provider, access_token=tokens.access_token)
    except (OAuthConfigurationError, OAuthExchangeError) as error:
        log.error("email.oauth_exchange_failed", provider=provider, error=str(error))
        return _html_result(ok=False, message=f"Could not finish connecting: {error}")

    connection_id = _connection_id(provider, profile.address)
    await TokenVault(db).store(
        connection_id,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in_seconds=tokens.expires_in_seconds,
    )
    await save_connection_record(
        db,
        connection_id=connection_id,
        provider=provider,
        display_name=f"{_PROVIDER_DISPLAY_NAMES[provider]} ({profile.address})",
        address=profile.address,
    )

    service = _services(request).get("email")
    if service is not None:
        existing = service.connections.get(connection_id)
        service.add_connection(
            EmailConnection(
                id=connection_id,
                provider=provider,
                display_name=f"{_PROVIDER_DISPLAY_NAMES[provider]} ({profile.address})",
                address=profile.address,
                # Reconnecting an existing connection keeps whatever an
                # operator already set; a brand-new one starts refused —
                # the same deployment-level kill switch every other
                # connection has, requiring an explicit second step.
                allow_send=existing.allow_send if existing is not None else False,
                credentials={
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "expires_at": (
                        datetime.now(UTC) + timedelta(seconds=tokens.expires_in_seconds)
                    ).isoformat(),
                },
            )
        )

    log.info("email.oauth_connected", provider=provider, connection_id=connection_id)
    return _html_result(ok=True, message=f"Connected {profile.address} ({_PROVIDER_DISPLAY_NAMES[provider]}).")


class AllowSendUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_send: bool


@router.patch("/email/connections/{connection_id}")
async def update_connection(
    connection_id: str,
    body: AllowSendUpdate,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """The only lever to actually let a connection send — every connection
    (OAuth-established or static) starts refused; this is the explicit
    second step, same reasoning as every other write-capable connection in
    this platform (MCP, F&O)."""
    del user
    db = _require_db(request)
    updated = await set_allow_send(db, connection_id, body.allow_send)
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"No OAuth-established connection {connection_id!r} — "
            "a static (env-var-configured) connection's allow_send is set "
            "in EMAIL_CONNECTIONS, not here.",
        )
    service = _services(request).get("email")
    if service is not None:
        existing = service.connections.get(connection_id)
        if existing is not None:
            service.add_connection(existing.model_copy(update={"allow_send": body.allow_send}))
    return {"id": connection_id, "allow_send": body.allow_send}


@router.delete("/email/connections/{connection_id}")
async def disconnect(
    connection_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    del user
    db = _require_db(request)
    await delete_connection_record(db, connection_id)
    service = _services(request).get("email")
    if service is not None:
        service.remove_connection(connection_id)
    return {"id": connection_id, "removed": True}
