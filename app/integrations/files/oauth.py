"""OAuth authorization-code flow for Google Drive and OneDrive.

Pure, stateless provider functions — no database access here, mirroring
app/integrations/email/oauth.py exactly, including reuse of the SAME Google
Cloud / Azure AD app registrations (`google_oauth_client_id/secret`,
`microsoft_oauth_client_id/secret` in app/config.py) with a different
requested scope set per feature. app/api/integration_oauth.py owns the
state/PKCE persistence and orchestration.

Adding Drive scopes and this module's redirect URIs
(`/api/builder/integrations/oauth/callback/{google_drive,onedrive}`) to the
existing OAuth app registrations is an external, deployment-owner
prerequisite this module cannot perform.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.observability.logging import get_logger

log = get_logger(__name__)

Provider = Literal["google_drive", "onedrive"]

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/userinfo.email"
)
_MICROSOFT_SCOPES = "offline_access Files.Read.All User.Read"


class OAuthConfigurationError(RuntimeError):
    """This provider has no client id/secret configured for this deployment."""


class OAuthExchangeError(RuntimeError):
    """The provider rejected the code/refresh-token exchange."""


@dataclass
class TokenSet:
    """Provides the TokenSet behaviour.

    Attributes:
        access_token (str).
        refresh_token (str | None).
        expires_in_seconds (int).
    """
    access_token: str
    refresh_token: str | None
    expires_in_seconds: int


@dataclass
class Profile:
    """Provides the Profile behaviour.

    Attributes:
        address (str).
    """
    address: str


def _redirect_uri(provider: Provider) -> str:
    # Must exactly match the path app/api/integration_oauth.py's callback
    # route is mounted at, and the URI registered with the Azure AD app /
    # Google Cloud OAuth client — a distinct redirect URI from the email
    # integration's, even though both reuse the same client id/secret.
    """Internal helper for the redirect uri step.

    Args:
        provider (Provider): Provider name.

    Returns:
        str: The uri.
    """
    return f"{settings.oauth_redirect_base_url.rstrip('/')}/api/builder/integrations/oauth/callback/{provider}"


def generate_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) for Google's PKCE requirement."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(provider: Provider, *, state: str, code_challenge: str | None = None) -> str:
    """Authorize the url.

    Args:
        provider (Provider): Provider name.
        state (str): Current workflow state.
        code_challenge (str | None): The code challenge (optional, default None).

    Returns:
        str: The url.
    """
    if provider == "onedrive":
        if not settings.microsoft_oauth_client_id:
            raise OAuthConfigurationError(
                "microsoft_oauth_client_id is not configured for this deployment"
            )
        params = {
            "client_id": settings.microsoft_oauth_client_id,
            "response_type": "code",
            "redirect_uri": _redirect_uri(provider),
            "response_mode": "query",
            "scope": _MICROSOFT_SCOPES,
            "state": state,
        }
        tenant = settings.microsoft_oauth_tenant_id or "common"
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"

    if not settings.google_oauth_client_id:
        raise OAuthConfigurationError(
            "google_oauth_client_id is not configured for this deployment"
        )
    params = {
        "client_id": settings.google_oauth_client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(provider),
        "scope": _GOOGLE_SCOPES,
        "state": state,
        "access_type": "offline",
        # Forces a refresh_token on every consent, not just the first —
        # without this a user who reconnects after revoking access silently
        # gets no refresh_token and the connection can't outlive one hour.
        "prompt": "consent",
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_code(
    provider: Provider,
    *,
    code: str,
    code_verifier: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> TokenSet:
    """Compute the exchange code.

    Args:
        provider (Provider): Provider name.
        code (str): The code.
        code_verifier (str | None): The code verifier (optional, default None).
        client (httpx.AsyncClient | None): Client instance (optional, default None).

    Returns:
        TokenSet: The code.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        if provider == "onedrive":
            tenant = settings.microsoft_oauth_tenant_id or "common"
            response = await client.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "client_id": settings.microsoft_oauth_client_id,
                    "client_secret": settings.microsoft_oauth_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _redirect_uri(provider),
                    "scope": _MICROSOFT_SCOPES,
                },
            )
        else:
            data = {
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(provider),
            }
            if code_verifier:
                data["code_verifier"] = code_verifier
            response = await client.post("https://oauth2.googleapis.com/token", data=data)
        return _parse_token_response(provider, response)
    finally:
        if owns_client:
            await client.aclose()


async def refresh_access_token(
    provider: Provider,
    *,
    refresh_token: str,
    client: httpx.AsyncClient | None = None,
) -> TokenSet:
    """Refresh the access token.

    Args:
        provider (Provider): Provider name.
        refresh_token (str): The refresh token.
        client (httpx.AsyncClient | None): Client instance (optional, default None).

    Returns:
        TokenSet: The access token.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        if provider == "onedrive":
            tenant = settings.microsoft_oauth_tenant_id or "common"
            response = await client.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "client_id": settings.microsoft_oauth_client_id,
                    "client_secret": settings.microsoft_oauth_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": _MICROSOFT_SCOPES,
                },
            )
        else:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        result = _parse_token_response(provider, response)
        # Neither provider reliably re-issues a refresh_token on refresh —
        # the caller must keep using the one it already has when this is None.
        if not result.refresh_token:
            result.refresh_token = refresh_token
        return result
    finally:
        if owns_client:
            await client.aclose()


def _parse_token_response(provider: Provider, response: httpx.Response) -> TokenSet:
    """Parse the token response.

    Args:
        provider (Provider): Provider name.
        response (httpx.Response): Outgoing FastAPI response.

    Returns:
        TokenSet: The token response.
    """
    if response.status_code >= 400:
        raise OAuthExchangeError(
            f"{provider} token endpoint returned {response.status_code}: "
            f"{response.text[:300]}"
        )
    body = response.json()
    access_token = body.get("access_token")
    if not access_token:
        raise OAuthExchangeError(f"{provider} token response had no access_token")
    return TokenSet(
        access_token=access_token,
        refresh_token=body.get("refresh_token"),
        expires_in_seconds=int(body.get("expires_in", 3600)),
    )


async def fetch_profile(
    provider: Provider,
    *,
    access_token: str,
    client: httpx.AsyncClient | None = None,
) -> Profile:
    """Fetch the profile.

    Args:
        provider (Provider): Provider name.
        access_token (str): The access token.
        client (httpx.AsyncClient | None): Client instance (optional, default None).

    Returns:
        Profile: The profile.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT)
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        if provider == "onedrive":
            response = await client.get("https://graph.microsoft.com/v1.0/me", headers=headers)
            if response.status_code >= 400:
                raise OAuthExchangeError(
                    f"onedrive profile lookup returned {response.status_code}: "
                    f"{response.text[:300]}"
                )
            body = response.json()
            address = body.get("mail") or body.get("userPrincipalName") or ""
        else:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo", headers=headers
            )
            if response.status_code >= 400:
                raise OAuthExchangeError(
                    f"google_drive profile lookup returned {response.status_code}: "
                    f"{response.text[:300]}"
                )
            address = response.json().get("email") or ""
        if not address:
            raise OAuthExchangeError(f"{provider} profile lookup returned no account address")
        return Profile(address=address)
    finally:
        if owns_client:
            await client.aclose()
