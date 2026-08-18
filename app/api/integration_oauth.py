"""Integration OAuth connect flow — Google Drive and OneDrive.

Mounted alongside the rest of the Builder API (same /api/builder prefix,
same auth dependency). Mirrors app/api/email_oauth.py: the authorization-code
round trip, connect/disconnect, plus read-only "browse" endpoints the
Builder's file-picker UI calls live while configuring a node — distinct from
workflow execution, no run involved.

`state` doubles as CSRF protection and (for Google) carries the PKCE code
verifier across the redirect round trip — a short-lived Mongo document,
single-use (deleted the moment the callback consumes it).
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from app.integrations.files.base import IntegrationAuthError, IntegrationNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.integrations.files import oauth
from app.integrations.files.base import IntegrationConnection
from app.integrations.files.connections_store import (
    delete_connection_record,
    save_connection_record,
)
from app.integrations.files.oauth import OAuthConfigurationError, OAuthExchangeError
from app.integrations.files.token_vault import TokenVault
from app.observability.logging import get_logger
from app.security.dependencies import CurrentUser, require_consultant
from app.security.entity_protection_errors import VaultKeyMisconfiguredError

log = get_logger(__name__)

router = APIRouter(prefix="/api/builder", tags=["integration-oauth"])

_STATES_COLLECTION = "integration_oauth_pending_states"
_STATE_TTL = timedelta(minutes=10)

Provider = Literal["google_drive", "onedrive"]

_PROVIDER_DISPLAY_NAMES: dict[Provider, str] = {
    "google_drive": "Google Drive",
    "onedrive": "OneDrive",
}


async def ensure_indexes(db: Any) -> None:
    """TTL-expires an abandoned connect attempt's pending state."""
    await db[_STATES_COLLECTION].create_index([("expires_at", 1)], expireAfterSeconds=0)


def _services(request: Request) -> dict[str, Any]:
    return getattr(request.app.state, "services", {}) or {}


def _require_db(request: Request) -> Any:
    db = _services(request).get("audit_db")
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="No database is configured in this deployment — integration connections need one to persist.",
        )
    return db


def _require_integration_service(request: Request) -> Any:
    service = _services(request).get("files_integration")
    if service is None:
        raise HTTPException(status_code=503, detail="File integration is not configured in this deployment.")
    return service


def _connection_id(provider: Provider, address: str) -> str:
    # Stable per (provider, account) — reconnecting the same account updates
    # its existing connection rather than creating a duplicate.
    normalized = address.strip().lower().replace("@", "_at_").replace(".", "_")
    return f"{provider}_{normalized}" if normalized else f"{provider}_{secrets.token_hex(6)}"


def _html_result(*, ok: bool, message: str) -> HTMLResponse:
    """A minimal, self-contained page for the OAuth popup window."""
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
try {{ if (window.opener) {{ window.opener.postMessage({{ type: "integration-oauth-complete", ok: {"true" if ok else "false"} }}, "*"); }} }} catch (e) {{}}
</script>
</body></html>"""
    return HTMLResponse(content=body, status_code=200 if ok else 400)


@router.get("/integrations/connect/{provider}")
async def connect(
    provider: Provider,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> RedirectResponse:
    """Starts the authorization-code flow: stash a one-time state (+ PKCE
    verifier for Google), redirect the user's browser to the provider."""
    del user
    db = _require_db(request)

    state = secrets.token_urlsafe(32)
    code_verifier: str | None = None
    code_challenge: str | None = None
    if provider == "google_drive":
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


@router.get("/integrations/oauth/callback/{provider}")
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
        log.warning("integration.oauth_denied", provider=provider, error=error)
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
        log.error("integration.oauth_exchange_failed", provider=provider, error=str(error))
        return _html_result(ok=False, message=f"Could not finish connecting: {error}")

    connection_id = _connection_id(provider, profile.address)
    try:
        await TokenVault(db).store(
            connection_id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in_seconds=tokens.expires_in_seconds,
        )
    except VaultKeyMisconfiguredError as error:
        log.error("integration.oauth_vault_misconfigured", provider=provider, error=str(error))
        return _html_result(ok=False, message=f"Could not finish connecting: {error}")
    await save_connection_record(
        db,
        connection_id=connection_id,
        provider=provider,
        display_name=f"{_PROVIDER_DISPLAY_NAMES[provider]} ({profile.address})",
        address=profile.address,
    )

    service = _services(request).get("files_integration")
    if service is not None:
        service.add_connection(
            IntegrationConnection(
                id=connection_id,
                provider=provider,
                display_name=f"{_PROVIDER_DISPLAY_NAMES[provider]} ({profile.address})",
                address=profile.address,
                needs_reauth=False,
                credentials={
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "expires_at": (
                        datetime.now(UTC) + timedelta(seconds=tokens.expires_in_seconds)
                    ).isoformat(),
                },
            )
        )

    log.info("integration.oauth_connected", provider=provider, connection_id=connection_id)
    return _html_result(ok=True, message=f"Connected {profile.address} ({_PROVIDER_DISPLAY_NAMES[provider]}).")


@router.get("/integrations/connections")
async def list_connections(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    del user
    service = _services(request).get("files_integration")
    if service is None:
        return {"connections": [], "configured": False}
    return {"connections": service.describe_connections(), "configured": bool(service.connections)}


@router.delete("/integrations/connections/{connection_id}")
async def disconnect(
    connection_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    del user
    db = _require_db(request)
    await delete_connection_record(db, connection_id)
    service = _services(request).get("files_integration")
    if service is not None:
        service.remove_connection(connection_id)
    return {"id": connection_id, "removed": True}


@router.get("/integrations/connections/{connection_id}/files")
async def browse_files(
    connection_id: str,
    request: Request,
    folder_id: str | None = None,
    query: str = "",
    page_size: int = 25,
    page_token: str | None = None,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Live folder/search browsing for the node config panel's file picker
    — not workflow execution, just a thin proxy through the service for
    whichever operation the presence of `query` selects."""
    del user
    service = _require_integration_service(request)
    try:
        result = await service.execute(
            connection_id=connection_id,
            operation="search_files" if query else "list_folder",
            folder_id=folder_id,
            query=query,
            page_size=page_size,
            page_token=page_token,
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "files": [item.model_dump() for item in result.files],
        "next_page_token": result.next_page_token,
    }


@router.get("/integrations/connections/{connection_id}/path/{file_id}")
async def browse_path(
    connection_id: str,
    file_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Breadcrumb chain for the file browser: walk `parent_id` up from
    `file_id` via repeated lookups, bounded so a cyclic/misbehaving provider
    response can never hang this request."""
    del user
    service = _require_integration_service(request)
    connection = service.connection(connection_id)
    provider = service.provider_for(connection)

    chain: list[dict[str, Any]] = []
    current_id: str | None = file_id
    seen: set[str] = set()
    for _ in range(25):
        if not current_id or current_id in seen:
            break
        seen.add(current_id)
        try:
            meta = await provider.get_file_meta(connection, file_id=current_id)
        except Exception:
            break
        chain.append(meta.model_dump())
        current_id = meta.parent_id
    chain.reverse()
    return {"path": chain}


@router.get("/integrations/connections/{connection_id}/download/{file_id}")
async def download_file(
    connection_id: str,
    file_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> Response:
    """Streams one file's real bytes straight to the browser — a plain
    navigation (an `<a href>` click, not a fetch), authenticated by the same
    HttpOnly cookie the OAuth popup flow relies on. Distinct from the node's
    own get_file operation: this never touches object storage, since the
    bytes only need to reach this one response, not survive into workflow
    state."""
    del user
    service = _require_integration_service(request)
    try:
        result = await service.execute(
            connection_id=connection_id, operation="get_file", file_id=file_id,
        )
    except IntegrationAuthError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except IntegrationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    downloaded = result.downloaded
    if downloaded is None:
        raise HTTPException(status_code=502, detail="Provider returned no content for this file.")

    ascii_fallback = downloaded.meta.name.encode("ascii", "ignore").decode("ascii") or "download"
    encoded_name = quote(downloaded.meta.name)
    return Response(
        content=downloaded.content,
        media_type=downloaded.content_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_fallback}"; '
                f"filename*=UTF-8''{encoded_name}"
            ),
        },
    )
