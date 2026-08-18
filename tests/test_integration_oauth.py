"""Integration OAuth: token vault encryption, connection persistence, and the
authorization-code round trip (mocked HTTP, real Google/Microsoft are
external prerequisites — see app/config.py's google_oauth_*/microsoft_oauth_*
settings, reused from the email integration with a different scope set).
Mirrors tests/test_email_oauth.py's shape.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import settings
from app.integrations.files import oauth
from app.integrations.files.base import IntegrationAuthError, IntegrationConnection
from app.integrations.files.connections_store import (
    delete_connection_record,
    load_dynamic_connections,
    save_connection_record,
    set_needs_reauth,
)
from app.integrations.files.oauth import OAuthConfigurationError, OAuthExchangeError
from app.integrations.files.service import IntegrationService
from app.integrations.files.token_vault import TokenVault
from app.security.entity_protection_errors import VaultKeyMisconfiguredError
from tests.security._fake_mongo import FakeAsyncDatabase

TEST_KEY = "g" * 40  # >=32 bytes, not a placeholder, distinct from the other vault keys


@pytest.fixture()
def vault_key():
    original = settings.integration_token_vault_master_key
    settings.integration_token_vault_master_key = TEST_KEY
    yield
    settings.integration_token_vault_master_key = original


@pytest.fixture()
def db(vault_key) -> FakeAsyncDatabase:
    return FakeAsyncDatabase()


# --------------------------------------------------------------------------
# Token vault
# --------------------------------------------------------------------------

async def test_seal_unseal_round_trip(db: FakeAsyncDatabase):
    vault = TokenVault(db)
    await vault.store("conn1", access_token="tok", refresh_token="refresh", expires_in_seconds=3600)
    loaded = await vault.load("conn1")
    assert loaded["access_token"] == "tok"
    assert loaded["refresh_token"] == "refresh"
    assert loaded["expires_at"] > datetime.now(UTC)


async def test_load_missing_connection_returns_none(db: FakeAsyncDatabase):
    assert await TokenVault(db).load("nothing-here") is None


async def test_forget_removes_the_stored_tokens(db: FakeAsyncDatabase):
    vault = TokenVault(db)
    await vault.store("conn1", access_token="tok", refresh_token="refresh", expires_in_seconds=3600)
    await vault.forget("conn1")
    assert await vault.load("conn1") is None


def test_master_key_must_not_reuse_secret_key():
    from app.integrations.files.token_vault import _load_kek

    original_secret, original_vault = settings.secret_key, settings.integration_token_vault_master_key
    try:
        settings.secret_key = "e" * 40
        settings.integration_token_vault_master_key = "e" * 40
        with pytest.raises(VaultKeyMisconfiguredError, match="secret_key"):
            _load_kek()
    finally:
        settings.secret_key, settings.integration_token_vault_master_key = original_secret, original_vault


def test_master_key_must_not_reuse_entity_vault_master_key():
    from app.integrations.files.token_vault import _load_kek

    original_entity, original_vault = settings.entity_vault_master_key, settings.integration_token_vault_master_key
    try:
        settings.entity_vault_master_key = "f" * 40
        settings.integration_token_vault_master_key = "f" * 40
        with pytest.raises(VaultKeyMisconfiguredError, match="entity_vault_master_key"):
            _load_kek()
    finally:
        settings.entity_vault_master_key, settings.integration_token_vault_master_key = original_entity, original_vault


def test_master_key_must_not_reuse_email_token_vault_master_key():
    from app.integrations.files.token_vault import _load_kek

    original_email, original_vault = settings.email_token_vault_master_key, settings.integration_token_vault_master_key
    try:
        settings.email_token_vault_master_key = "h" * 40
        settings.integration_token_vault_master_key = "h" * 40
        with pytest.raises(VaultKeyMisconfiguredError, match="email_token_vault_master_key"):
            _load_kek()
    finally:
        settings.email_token_vault_master_key, settings.integration_token_vault_master_key = original_email, original_vault


# --------------------------------------------------------------------------
# Connection persistence
# --------------------------------------------------------------------------

async def test_save_and_load_dynamic_connection(db: FakeAsyncDatabase):
    await TokenVault(db).store("google_drive_a", access_token="tok", refresh_token="refresh", expires_in_seconds=3600)
    await save_connection_record(
        db, connection_id="google_drive_a", provider="google_drive",
        display_name="Google Drive (a@example.com)", address="a@example.com",
    )
    connections = await load_dynamic_connections(db)
    assert "google_drive_a" in connections
    conn = connections["google_drive_a"]
    assert conn.provider == "google_drive"
    assert conn.address == "a@example.com"
    assert conn.needs_reauth is False
    assert conn.credentials["access_token"] == "tok"


async def test_a_connection_missing_its_tokens_is_skipped_not_raised(db: FakeAsyncDatabase):
    await save_connection_record(
        db, connection_id="orphaned", provider="onedrive",
        display_name="OneDrive (orphan)", address="orphan@example.com",
    )
    connections = await load_dynamic_connections(db)
    assert "orphaned" not in connections


async def test_set_needs_reauth_marks_a_connection(db: FakeAsyncDatabase):
    await TokenVault(db).store("google_drive_a", access_token="tok", refresh_token="r", expires_in_seconds=3600)
    await save_connection_record(
        db, connection_id="google_drive_a", provider="google_drive",
        display_name="Google Drive", address="a@example.com",
    )
    await set_needs_reauth(db, "google_drive_a", True)
    connections = await load_dynamic_connections(db)
    assert connections["google_drive_a"].needs_reauth is True


async def test_reconnecting_clears_a_stale_needs_reauth_flag(db: FakeAsyncDatabase):
    await TokenVault(db).store("google_drive_a", access_token="tok", refresh_token="r", expires_in_seconds=3600)
    await save_connection_record(
        db, connection_id="google_drive_a", provider="google_drive",
        display_name="Google Drive", address="a@example.com",
    )
    await set_needs_reauth(db, "google_drive_a", True)
    # A fresh OAuth consent re-saves the record — this must clear the flag.
    await save_connection_record(
        db, connection_id="google_drive_a", provider="google_drive",
        display_name="Google Drive", address="a@example.com",
    )
    connections = await load_dynamic_connections(db)
    assert connections["google_drive_a"].needs_reauth is False


async def test_delete_connection_record_removes_both_record_and_tokens(db: FakeAsyncDatabase):
    await TokenVault(db).store("google_drive_a", access_token="tok", refresh_token="r", expires_in_seconds=3600)
    await save_connection_record(
        db, connection_id="google_drive_a", provider="google_drive",
        display_name="Google Drive", address="a@example.com",
    )
    await delete_connection_record(db, "google_drive_a")
    assert await load_dynamic_connections(db) == {}
    assert await TokenVault(db).load("google_drive_a") is None


# --------------------------------------------------------------------------
# OAuth provider round trip (mocked HTTP)
# --------------------------------------------------------------------------

@pytest.fixture()
def microsoft_configured():
    original = (
        settings.microsoft_oauth_client_id,
        settings.microsoft_oauth_client_secret,
        settings.oauth_redirect_base_url,
    )
    settings.microsoft_oauth_client_id = "test-client-id"
    settings.microsoft_oauth_client_secret = "test-client-secret"
    settings.oauth_redirect_base_url = "https://app.example.com"
    yield
    (
        settings.microsoft_oauth_client_id,
        settings.microsoft_oauth_client_secret,
        settings.oauth_redirect_base_url,
    ) = original


def test_authorize_url_requires_client_id_configured():
    original = settings.microsoft_oauth_client_id
    settings.microsoft_oauth_client_id = ""
    try:
        with pytest.raises(OAuthConfigurationError):
            oauth.authorize_url("onedrive", state="s")
    finally:
        settings.microsoft_oauth_client_id = original


def test_authorize_url_includes_state_redirect_uri_and_files_scope(microsoft_configured):
    url = oauth.authorize_url("onedrive", state="abc123")
    assert "state=abc123" in url
    assert "app.example.com" in url
    assert "login.microsoftonline.com" in url
    assert "Files.Read.All" in url


def test_google_drive_authorize_url_includes_pkce_challenge_and_drive_scope():
    original = (settings.google_oauth_client_id, settings.oauth_redirect_base_url)
    settings.google_oauth_client_id = "test-google-client"
    settings.oauth_redirect_base_url = "https://app.example.com"
    try:
        verifier, challenge = oauth.generate_pkce_pair()
        url = oauth.authorize_url("google_drive", state="xyz", code_challenge=challenge)
        assert f"code_challenge={challenge}" in url
        assert "code_challenge_method=S256" in url
        assert "drive.readonly" in url
        assert verifier != challenge
    finally:
        settings.google_oauth_client_id, settings.oauth_redirect_base_url = original


def test_the_redirect_uri_is_distinct_from_the_email_integrations():
    original = settings.oauth_redirect_base_url
    settings.oauth_redirect_base_url = "https://app.example.com"
    try:
        original_ms = settings.microsoft_oauth_client_id
        settings.microsoft_oauth_client_id = "test-client-id"
        url = oauth.authorize_url("onedrive", state="s")
        assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fapi%2Fbuilder%2Fintegrations%2Foauth%2Fcallback%2Fonedrive" in url
        settings.microsoft_oauth_client_id = original_ms
    finally:
        settings.oauth_redirect_base_url = original


async def test_exchange_code_parses_a_successful_token_response(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth2/v2.0/token")
        return httpx.Response(200, json={
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = await oauth.exchange_code("onedrive", code="auth-code", client=client)
    assert tokens.access_token == "new-access-token"
    assert tokens.refresh_token == "new-refresh-token"
    assert tokens.expires_in_seconds == 3600


async def test_exchange_code_raises_on_provider_error(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthExchangeError):
        await oauth.exchange_code("onedrive", code="bad-code", client=client)


async def test_fetch_profile_reads_the_account_address(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/me")
        return httpx.Response(200, json={"mail": "person@example.com"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    profile = await oauth.fetch_profile("onedrive", access_token="tok", client=client)
    assert profile.address == "person@example.com"


# --------------------------------------------------------------------------
# IntegrationService's central refresh-before-call
# --------------------------------------------------------------------------

async def test_service_refreshes_a_near_expiry_connection_before_calling_the_adapter(
    db: FakeAsyncDatabase, microsoft_configured,
):
    class _FakeProvider:
        provider = "onedrive"
        seen_token: str | None = None

        async def list_folder(self, connection, *, folder_id, page_size, page_token):
            self.seen_token = connection.credentials.get("access_token")
            from app.integrations.files.base import Page
            return Page(items=[], next_page_token=None)

    provider = _FakeProvider()
    near_expiry = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    connection = IntegrationConnection(
        id="onedrive_a", provider="onedrive", address="a@example.com",
        credentials={"access_token": "stale-token", "refresh_token": "refresh-me", "expires_at": near_expiry},
    )
    service = IntegrationService(providers={"onedrive": provider}, connections={"onedrive_a": connection}, db=db)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "fresh-token", "refresh_token": "refresh-me", "expires_in": 3600})

    import app.integrations.files.oauth as oauth_module
    original_refresh = oauth_module.refresh_access_token

    async def patched_refresh(provider_name, *, refresh_token, client=None):
        return await original_refresh(
            provider_name, refresh_token=refresh_token,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    oauth_module.refresh_access_token = patched_refresh
    try:
        await service.execute(connection_id="onedrive_a", operation="list_folder")
    finally:
        oauth_module.refresh_access_token = original_refresh

    assert provider.seen_token == "fresh-token"
    assert service.connections["onedrive_a"].credentials["access_token"] == "fresh-token"
    persisted = await TokenVault(db).load("onedrive_a")
    assert persisted["access_token"] == "fresh-token"


async def test_service_marks_needs_reauth_when_refresh_fails(db: FakeAsyncDatabase, microsoft_configured):
    near_expiry = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    connection = IntegrationConnection(
        id="onedrive_a", provider="onedrive", address="a@example.com",
        credentials={"access_token": "stale", "refresh_token": "bad-refresh", "expires_at": near_expiry},
    )
    await TokenVault(db).store("onedrive_a", access_token="stale", refresh_token="bad-refresh", expires_in_seconds=-1)
    await save_connection_record(
        db, connection_id="onedrive_a", provider="onedrive",
        display_name="OneDrive", address="a@example.com",
    )
    service = IntegrationService(providers={"onedrive": object()}, connections={"onedrive_a": connection}, db=db)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    import app.integrations.files.oauth as oauth_module
    original_refresh = oauth_module.refresh_access_token

    async def patched_refresh(provider_name, *, refresh_token, client=None):
        return await original_refresh(
            provider_name, refresh_token=refresh_token,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    oauth_module.refresh_access_token = patched_refresh
    try:
        with pytest.raises(IntegrationAuthError, match="expired"):
            await service.execute(connection_id="onedrive_a", operation="list_folder")
    finally:
        oauth_module.refresh_access_token = original_refresh

    assert service.connections["onedrive_a"].needs_reauth is True
    connections = await load_dynamic_connections(db)
    assert connections["onedrive_a"].needs_reauth is True
