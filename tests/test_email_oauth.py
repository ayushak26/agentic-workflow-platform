"""Email OAuth: token vault encryption, connection persistence, and the
authorization-code round trip (mocked HTTP, real Microsoft/Google are
external prerequisites — see app/config.py's microsoft_oauth_*/google_oauth_*
settings). Mirrors tests/security/test_entity_vault.py's shape for the
vault half, and app/integrations/email/msgraph.py's own tests'
httpx.MockTransport pattern for the provider-HTTP half.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import settings
from app.integrations.email import oauth
from app.integrations.email.base import EmailConnection
from app.integrations.email.connections_store import (
    delete_connection_record,
    load_dynamic_connections,
    save_connection_record,
    set_allow_send,
)
from app.integrations.email.oauth import OAuthConfigurationError, OAuthExchangeError
from app.integrations.email.service import EmailConnectionError, EmailService
from app.integrations.email.token_vault import TokenVault
from app.security.entity_protection_errors import VaultKeyMisconfiguredError
from tests.security._fake_mongo import FakeAsyncDatabase

TEST_KEY = "c" * 40  # >=32 bytes, not a placeholder, distinct from the other vault keys


@pytest.fixture()
def vault_key():
    original = settings.email_token_vault_master_key
    settings.email_token_vault_master_key = TEST_KEY
    yield
    settings.email_token_vault_master_key = original


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


async def test_wrong_key_cannot_unseal_a_token_sealed_under_another_key(db: FakeAsyncDatabase):
    vault = TokenVault(db)
    await vault.store("conn1", access_token="tok", refresh_token="refresh", expires_in_seconds=3600)
    settings.email_token_vault_master_key = "d" * 40  # a different, still-valid key
    with pytest.raises(Exception):
        await vault.load("conn1")


def test_master_key_must_not_reuse_secret_key():
    from app.integrations.email.token_vault import _load_kek

    original_secret, original_vault = settings.secret_key, settings.email_token_vault_master_key
    try:
        settings.secret_key = "e" * 40
        settings.email_token_vault_master_key = "e" * 40
        with pytest.raises(VaultKeyMisconfiguredError, match="secret_key"):
            _load_kek()
    finally:
        settings.secret_key, settings.email_token_vault_master_key = original_secret, original_vault


def test_master_key_must_not_reuse_entity_vault_master_key():
    from app.integrations.email.token_vault import _load_kek

    original_entity, original_vault = settings.entity_vault_master_key, settings.email_token_vault_master_key
    try:
        settings.entity_vault_master_key = "f" * 40
        settings.email_token_vault_master_key = "f" * 40
        with pytest.raises(VaultKeyMisconfiguredError, match="entity_vault_master_key"):
            _load_kek()
    finally:
        settings.entity_vault_master_key, settings.email_token_vault_master_key = original_entity, original_vault


# --------------------------------------------------------------------------
# Connection persistence
# --------------------------------------------------------------------------

async def test_save_and_load_dynamic_connection(db: FakeAsyncDatabase):
    await TokenVault(db).store("microsoft_a", access_token="tok", refresh_token="refresh", expires_in_seconds=3600)
    await save_connection_record(
        db, connection_id="microsoft_a", provider="microsoft",
        display_name="Outlook (a@example.com)", address="a@example.com",
    )
    connections = await load_dynamic_connections(db)
    assert "microsoft_a" in connections
    conn = connections["microsoft_a"]
    assert conn.provider == "microsoft"
    assert conn.address == "a@example.com"
    assert conn.allow_send is False  # every connection starts refused
    assert conn.credentials["access_token"] == "tok"


async def test_a_connection_missing_its_tokens_is_skipped_not_raised(db: FakeAsyncDatabase):
    # Simulates a record write succeeding but the token write failing (or a
    # forget() that removed tokens without removing the record) — must not
    # take down the whole connections list.
    await save_connection_record(
        db, connection_id="orphaned", provider="microsoft",
        display_name="Outlook (orphan)", address="orphan@example.com",
    )
    connections = await load_dynamic_connections(db)
    assert "orphaned" not in connections


async def test_set_allow_send_updates_an_existing_connection(db: FakeAsyncDatabase):
    await TokenVault(db).store("microsoft_a", access_token="tok", refresh_token="r", expires_in_seconds=3600)
    await save_connection_record(
        db, connection_id="microsoft_a", provider="microsoft",
        display_name="Outlook", address="a@example.com",
    )
    updated = await set_allow_send(db, "microsoft_a", True)
    assert updated is True
    connections = await load_dynamic_connections(db)
    assert connections["microsoft_a"].allow_send is True


async def test_set_allow_send_on_unknown_connection_reports_not_updated(db: FakeAsyncDatabase):
    assert await set_allow_send(db, "does-not-exist", True) is False


async def test_delete_connection_record_removes_both_record_and_tokens(db: FakeAsyncDatabase):
    await TokenVault(db).store("microsoft_a", access_token="tok", refresh_token="r", expires_in_seconds=3600)
    await save_connection_record(
        db, connection_id="microsoft_a", provider="microsoft",
        display_name="Outlook", address="a@example.com",
    )
    await delete_connection_record(db, "microsoft_a")
    assert await load_dynamic_connections(db) == {}
    assert await TokenVault(db).load("microsoft_a") is None


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
            oauth.authorize_url("microsoft", state="s")
    finally:
        settings.microsoft_oauth_client_id = original


def test_authorize_url_includes_state_and_redirect_uri(microsoft_configured):
    url = oauth.authorize_url("microsoft", state="abc123")
    assert "state=abc123" in url
    assert "app.example.com" in url
    assert "login.microsoftonline.com" in url


def test_gmail_authorize_url_includes_pkce_challenge():
    original = (settings.google_oauth_client_id, settings.oauth_redirect_base_url)
    settings.google_oauth_client_id = "test-google-client"
    settings.oauth_redirect_base_url = "https://app.example.com"
    try:
        verifier, challenge = oauth.generate_pkce_pair()
        url = oauth.authorize_url("gmail", state="xyz", code_challenge=challenge)
        assert f"code_challenge={challenge}" in url
        assert "code_challenge_method=S256" in url
        assert verifier != challenge
    finally:
        settings.google_oauth_client_id, settings.oauth_redirect_base_url = original


async def test_exchange_code_parses_a_successful_token_response(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth2/v2.0/token")
        return httpx.Response(200, json={
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = await oauth.exchange_code("microsoft", code="auth-code", client=client)
    assert tokens.access_token == "new-access-token"
    assert tokens.refresh_token == "new-refresh-token"
    assert tokens.expires_in_seconds == 3600


async def test_exchange_code_raises_on_provider_error(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthExchangeError):
        await oauth.exchange_code("microsoft", code="bad-code", client=client)


async def test_refresh_keeps_the_old_refresh_token_if_none_reissued(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "refreshed-token", "expires_in": 3600})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = await oauth.refresh_access_token("microsoft", refresh_token="original-refresh", client=client)
    assert tokens.access_token == "refreshed-token"
    assert tokens.refresh_token == "original-refresh"


async def test_fetch_profile_reads_the_mailbox_address(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/me")
        return httpx.Response(200, json={"mail": "person@example.com"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    profile = await oauth.fetch_profile("microsoft", access_token="tok", client=client)
    assert profile.address == "person@example.com"


async def test_fetch_profile_raises_when_the_provider_gives_no_address(microsoft_configured):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthExchangeError):
        await oauth.fetch_profile("microsoft", access_token="tok", client=client)


# --------------------------------------------------------------------------
# EmailService's central refresh-before-call
# --------------------------------------------------------------------------

async def test_service_refreshes_a_near_expiry_connection_before_calling_the_adapter(
    db: FakeAsyncDatabase, microsoft_configured,
):
    class _FakeAdapter:
        provider = "microsoft"
        seen_token: str | None = None

        async def search(self, connection, criteria):
            self.seen_token = connection.credentials.get("access_token")
            return []

    adapter = _FakeAdapter()
    near_expiry = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    connection = EmailConnection(
        id="microsoft_a", provider="microsoft", address="a@example.com", allow_send=True,
        credentials={"access_token": "stale-token", "refresh_token": "refresh-me", "expires_at": near_expiry},
    )
    service = EmailService(adapters={"microsoft": adapter}, connections={"microsoft_a": connection}, db=db)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "fresh-token", "refresh_token": "refresh-me", "expires_in": 3600})

    import app.integrations.email.oauth as oauth_module
    original_refresh = oauth_module.refresh_access_token

    async def patched_refresh(provider, *, refresh_token, client=None):
        return await original_refresh(
            provider, refresh_token=refresh_token,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    oauth_module.refresh_access_token = patched_refresh
    try:
        await service.execute(connection_id="microsoft_a", operation="search")
    finally:
        oauth_module.refresh_access_token = original_refresh

    assert adapter.seen_token == "fresh-token"
    assert service.connections["microsoft_a"].credentials["access_token"] == "fresh-token"
    persisted = await TokenVault(db).load("microsoft_a")
    assert persisted["access_token"] == "fresh-token"


async def test_service_leaves_a_fresh_connection_untouched():
    class _FakeAdapter:
        provider = "microsoft"
        seen_token: str | None = None

        async def search(self, connection, criteria):
            self.seen_token = connection.credentials.get("access_token")
            return []

    adapter = _FakeAdapter()
    far_future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    connection = EmailConnection(
        id="microsoft_a", provider="microsoft", address="a@example.com", allow_send=True,
        credentials={"access_token": "still-good", "refresh_token": "r", "expires_at": far_future},
    )
    service = EmailService(adapters={"microsoft": adapter}, connections={"microsoft_a": connection})
    await service.execute(connection_id="microsoft_a", operation="search")
    assert adapter.seen_token == "still-good"


async def test_service_raises_a_clear_error_when_refresh_fails(microsoft_configured):
    near_expiry = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    connection = EmailConnection(
        id="microsoft_a", provider="microsoft", address="a@example.com", allow_send=True,
        credentials={"access_token": "stale", "refresh_token": "bad-refresh", "expires_at": near_expiry},
    )
    service = EmailService(adapters={"microsoft": object()}, connections={"microsoft_a": connection})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    import app.integrations.email.oauth as oauth_module
    original_refresh = oauth_module.refresh_access_token

    async def patched_refresh(provider, *, refresh_token, client=None):
        return await original_refresh(
            provider, refresh_token=refresh_token,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    oauth_module.refresh_access_token = patched_refresh
    try:
        with pytest.raises(EmailConnectionError, match="expired"):
            await service.execute(connection_id="microsoft_a", operation="search")
    finally:
        oauth_module.refresh_access_token = original_refresh
