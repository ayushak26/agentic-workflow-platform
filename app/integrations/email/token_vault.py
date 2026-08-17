"""Encrypted storage for OAuth-issued email tokens.

Mirrors app/security/entity_vault.py's envelope encryption exactly (AES-256-GCM,
a per-process KEK derived from its own master-key setting wrapping a fresh
random per-record DEK, AAD binding the ciphertext to its own connection id) —
same reasoning, a different security domain: a real Outlook/Gmail refresh
token is at least as sensitive as a tokenized entity mapping, and reusing
entity_vault's own key would mean one leaked key compromises both.

One Mongo collection (`email_oauth_tokens`), one document per connection id,
holding `{access_token, refresh_token, expires_at}` sealed together.
"""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import _is_placeholder, settings
from app.observability.logging import get_logger
from app.security.entity_protection_errors import VaultKeyMisconfiguredError

log = get_logger(__name__)

TOKENS_COLLECTION = "email_oauth_tokens"


class TokenVaultUnavailableError(RuntimeError):
    """The token vault's storage (Mongo) could not be reached."""


def _load_kek() -> bytes:
    """Derive the vault's Key Encryption Key from settings, failing closed.

    Checked lazily (on first real use, not at Settings() construction) so
    pytest/local dev without a real key configured still boots — but
    actually sealing or unsealing a token requires a valid one.
    """
    raw = settings.email_token_vault_master_key
    if not raw.strip() or _is_placeholder(raw) or len(raw.encode("utf-8")) < 32:
        raise VaultKeyMisconfiguredError(
            "email_token_vault_master_key is missing, a placeholder, or "
            "shorter than 32 bytes"
        )
    if raw == settings.secret_key:
        raise VaultKeyMisconfiguredError(
            "email_token_vault_master_key must not reuse secret_key — a "
            "leaked JWT signing secret must not also decrypt OAuth tokens"
        )
    if raw == settings.entity_vault_master_key:
        raise VaultKeyMisconfiguredError(
            "email_token_vault_master_key must not reuse entity_vault_master_key "
            "— key separation across security domains, same reasoning as "
            "secret_key above"
        )
    return hashlib.sha256(b"email-token-vault-kek-v1:" + raw.encode("utf-8")).digest()


def _seal(payload: dict[str, Any], *, connection_id: str) -> dict[str, bytes]:
    import json

    dek = os.urandom(32)
    dek_nonce = os.urandom(12)
    wrapped_dek = AESGCM(_load_kek()).encrypt(dek_nonce, dek, None)
    nonce = os.urandom(12)
    aad = connection_id.encode("utf-8")
    plaintext = json.dumps(payload).encode("utf-8")
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
    return {
        "wrapped_dek": wrapped_dek,
        "dek_nonce": dek_nonce,
        "ciphertext": ciphertext,
        "nonce": nonce,
    }


def _unseal(doc: dict[str, Any], *, connection_id: str) -> dict[str, Any]:
    import json

    dek = AESGCM(_load_kek()).decrypt(
        bytes(doc["dek_nonce"]), bytes(doc["wrapped_dek"]), None
    )
    aad = connection_id.encode("utf-8")
    plaintext = AESGCM(dek).decrypt(bytes(doc["nonce"]), bytes(doc["ciphertext"]), aad)
    return json.loads(plaintext.decode("utf-8"))


class TokenVault:
    """Mongo-backed envelope-encrypted store for one connection's OAuth tokens."""

    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def _tokens(self):
        return self._db[TOKENS_COLLECTION]

    async def store(
        self,
        connection_id: str,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_in_seconds: int,
    ) -> None:
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
        sealed = _seal(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at.isoformat(),
            },
            connection_id=connection_id,
        )
        try:
            await self._tokens.update_one(
                {"_id": connection_id},
                {"$set": {**sealed, "updated_at": datetime.now(UTC)}},
                upsert=True,
            )
        except Exception as exc:
            raise TokenVaultUnavailableError(str(exc)) from exc

    async def load(self, connection_id: str) -> dict[str, Any] | None:
        """Returns `{access_token, refresh_token, expires_at}` (expires_at a
        real datetime), or None if this connection has no stored tokens."""
        try:
            doc = await self._tokens.find_one({"_id": connection_id})
        except Exception as exc:
            raise TokenVaultUnavailableError(str(exc)) from exc
        if doc is None:
            return None
        payload = _unseal(doc, connection_id=connection_id)
        payload["expires_at"] = datetime.fromisoformat(payload["expires_at"])
        return payload

    async def forget(self, connection_id: str) -> None:
        try:
            await self._tokens.delete_one({"_id": connection_id})
        except Exception as exc:
            raise TokenVaultUnavailableError(str(exc)) from exc
