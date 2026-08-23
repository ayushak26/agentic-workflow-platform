"""Encrypted, reversible placeholder<->original-value vault.

One Mongo collection (`entity_mappings`) serves as both the entity registry
(source of truth for placeholder assignment, manual + auto-detected entities
alike) and the reversible vault — there is no separate plaintext "registry"
table, since even operator-curated organisation/person names are treated as
confidential business information and are never stored unencrypted.

Envelope encryption: a per-process KEK (derived from
``settings.entity_vault_master_key``, deliberately NOT ``secret_key`` — see
``_load_kek`` — key separation) wraps a fresh random per-record DEK, which
seals the real value with AES-256-GCM. The AAD binds each ciphertext to its
own (session_id, collection_id, placeholder), so a raw Mongo write can't
reattach one entity's ciphertext to a different placeholder or session
without failing the GCM tag check.

Placeholder assignment is concurrency-safe: one Mongo document per entity
(keyed by a deterministic hash of its normalized value), an atomic ``$inc``
counter per (scope, entity_type) for numbering, and ``DuplicateKeyError``
handling to converge concurrent writers on a single winning mapping — see
``get_or_create_placeholder``.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import _is_placeholder, settings
from app.observability.logging import get_logger
from app.security.entity_protection_errors import (
    EntityVaultUnavailableError,
    VaultKeyMisconfiguredError,
)

log = get_logger(__name__)

ENTITY_MAPPINGS_COLLECTION = "entity_mappings"
ENTITY_COUNTERS_COLLECTION = "entity_placeholder_counters"


def _load_kek() -> bytes:
    """Derive the vault's Key Encryption Key from settings, failing closed.

    Checked lazily (on first real use), not at Settings() construction, so
    ``pytest``/local dev without Mongo configured still boots — but any
    actual attempt to protect a real workflow run (mode=pseudonymised is the
    default) requires a valid key.
    """
    raw = settings.entity_vault_master_key
    if (
        not raw.strip()
        or _is_placeholder(raw)
        or len(raw.encode("utf-8")) < 32
    ):
        raise VaultKeyMisconfiguredError(
            "entity_vault_master_key is missing, a placeholder, or shorter "
            "than 32 bytes"
        )
    if raw == settings.secret_key:
        raise VaultKeyMisconfiguredError(
            "entity_vault_master_key must not reuse secret_key — a leaked "
            "JWT signing secret must not also decrypt the entity vault"
        )
    # Single-purpose derivation (distinct label) so any sufficiently long
    # operator-supplied secret becomes a valid 32-byte AES-256 key, without
    # requiring base64/hex key material specifically.
    return hashlib.sha256(b"entity-vault-kek-v1:" + raw.encode("utf-8")).digest()


def _normalize(value: str) -> str:
    """Normalize the result.

    Args:
        value (str): Value to process.

    Returns:
        str: The result.
    """
    return " ".join(value.strip().casefold().split())


def entity_hash(entity_type: str, value: str) -> str:
    """Deterministic match key for one (type, normalized value) pair."""
    normalized = _normalize(value)
    return hashlib.sha256(f"{entity_type}\0{normalized}".encode("utf-8")).hexdigest()


def _mapping_id(session_id: str, collection_id: str, ehash: str) -> str:
    """Internal helper for the mapping id step.

    Args:
        session_id (str): Session scope the record belongs to.
        collection_id (str): Knowledge collection identifier.
        ehash (str): The ehash.

    Returns:
        str: The id.
    """
    return f"{session_id}::{collection_id}::{ehash}"


def _counter_id(session_id: str, collection_id: str, entity_type: str) -> str:
    """Internal helper for the counter id step.

    Args:
        session_id (str): Session scope the record belongs to.
        collection_id (str): Knowledge collection identifier.
        entity_type (str): The entity type.

    Returns:
        str: The id.
    """
    return f"{session_id}::{collection_id}::{entity_type}"


def _expiry() -> datetime:
    """Internal helper for the expiry step.

    Returns:
        datetime: The result.
    """
    return datetime.now(timezone.utc) + timedelta(
        seconds=settings.entity_mapping_ttl_seconds
    )


def _seal(
    real_value: str, *, session_id: str, collection_id: str, placeholder: str
) -> dict[str, bytes]:
    """Internal helper for the seal step.

    Args:
        real_value (str): The real value.
        session_id (str): Session scope the record belongs to.
        collection_id (str): Knowledge collection identifier.
        placeholder (str): The placeholder.

    Returns:
        dict[str, bytes]: The result.
    """
    dek = os.urandom(32)
    dek_nonce = os.urandom(12)
    wrapped_dek = AESGCM(_load_kek()).encrypt(dek_nonce, dek, None)
    nonce = os.urandom(12)
    aad = f"{session_id}\0{collection_id}\0{placeholder}".encode("utf-8")
    ciphertext = AESGCM(dek).encrypt(nonce, real_value.encode("utf-8"), aad)
    return {
        "wrapped_dek": wrapped_dek,
        "dek_nonce": dek_nonce,
        "ciphertext": ciphertext,
        "nonce": nonce,
    }


def _unseal(
    doc: dict[str, Any], *, session_id: str, collection_id: str, placeholder: str
) -> str:
    """Internal helper for the unseal step.

    Args:
        doc (dict[str, Any]): Document.
        session_id (str): Session scope the record belongs to.
        collection_id (str): Knowledge collection identifier.
        placeholder (str): The placeholder.

    Returns:
        str: The result.
    """
    dek = AESGCM(_load_kek()).decrypt(
        bytes(doc["dek_nonce"]), bytes(doc["wrapped_dek"]), None
    )
    aad = f"{session_id}\0{collection_id}\0{placeholder}".encode("utf-8")
    plaintext = AESGCM(dek).decrypt(bytes(doc["nonce"]), bytes(doc["ciphertext"]), aad)
    return plaintext.decode("utf-8")


class EntityVault:
    """Mongo-backed envelope-encrypted mapping vault, scoped per (session, collection)."""

    def __init__(self, db: Any) -> None:
        """Initialize the EntityVault.

        Args:
            db (Any): Mongo database handle.
        """
        self._db = db

    @property
    def _mappings(self):
        """The mappings."""
        return self._db[ENTITY_MAPPINGS_COLLECTION]

    @property
    def _counters(self):
        """The counters."""
        return self._db[ENTITY_COUNTERS_COLLECTION]

    async def ensure_indexes(self) -> None:
        """Ensure the indexes."""
        try:
            await self._mappings.create_index(
                [("expires_at", 1)], expireAfterSeconds=0
            )
            await self._mappings.create_index(
                [("session_id", 1), ("collection_id", 1), ("placeholder", 1)],
                unique=True,
                name="scope_placeholder_unique",
            )
            await self._mappings.create_index(
                [("session_id", 1), ("collection_id", 1), ("entity_type", 1)]
            )
            await self._counters.create_index(
                [("expires_at", 1)], expireAfterSeconds=0
            )
            log.debug("entity_vault.indexes_ensured")
        except Exception as exc:
            log.warning("entity_vault.index_setup_failed", error=str(exc))

    async def get_or_create_placeholder(
        self,
        *,
        session_id: str,
        collection_id: str,
        entity_type: str,
        real_value: str,
        source: str,
    ) -> str:
        """Stable placeholder for one entity within a scope.

        Concurrency-safe: same entity always converges to one placeholder,
        two different entities never collide, even under concurrent callers
        in the same scope (e.g. parallel LangGraph branches).
        """
        ehash = entity_hash(entity_type, real_value)
        _id = _mapping_id(session_id, collection_id, ehash)
        try:
            existing = await self._mappings.find_one({"_id": _id})
        except Exception as exc:
            raise EntityVaultUnavailableError(str(exc)) from exc
        if existing is not None:
            return existing["placeholder"]

        try:
            counter_doc = await self._counters.find_one_and_update(
                {"_id": _counter_id(session_id, collection_id, entity_type)},
                {"$inc": {"seq": 1}, "$set": {"expires_at": _expiry()}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except Exception as exc:
            raise EntityVaultUnavailableError(str(exc)) from exc
        seq = counter_doc["seq"]
        placeholder = f"[[ENTITY_{entity_type.upper()}_{seq}]]"

        sealed = _seal(
            real_value,
            session_id=session_id,
            collection_id=collection_id,
            placeholder=placeholder,
        )
        now = datetime.now(timezone.utc)
        doc = {
            "_id": _id,
            "session_id": session_id,
            "collection_id": collection_id,
            "entity_type": entity_type,
            "placeholder": placeholder,
            "source": source,
            "created_at": now,
            "expires_at": _expiry(),
            **sealed,
        }
        try:
            await self._mappings.insert_one(doc)
        except DuplicateKeyError:
            # Lost the race for this exact entity — converge on the winner's
            # placeholder rather than creating a second, divergent mapping.
            # Our issued sequence number is simply burned; that's harmless,
            # uniqueness (not compactness) is the requirement.
            winner = await self._mappings.find_one({"_id": _id})
            if winner is None:
                raise EntityVaultUnavailableError(
                    "mapping vanished immediately after a lost insert race"
                ) from None
            return winner["placeholder"]
        except Exception as exc:
            raise EntityVaultUnavailableError(str(exc)) from exc
        return placeholder

    async def list_scope_entities(
        self, *, session_id: str, collection_id: str
    ) -> list[dict[str, str]]:
        """Decrypted known entities for a scope — feeds the registry-first
        literal-match detection pass and the manual-registration listing API."""
        try:
            cursor = self._mappings.find(
                {"session_id": session_id, "collection_id": collection_id}
            )
            docs = await cursor.to_list(length=10_000)
        except Exception as exc:
            raise EntityVaultUnavailableError(str(exc)) from exc
        results: list[dict[str, str]] = []
        for doc in docs:
            try:
                value = _unseal(
                    doc,
                    session_id=session_id,
                    collection_id=collection_id,
                    placeholder=doc["placeholder"],
                )
            except Exception as exc:
                raise VaultKeyMisconfiguredError(
                    f"failed to decrypt mapping {doc['_id']!r}: {exc}"
                ) from exc
            results.append(
                {
                    "placeholder": doc["placeholder"],
                    "entity_type": doc["entity_type"],
                    "value": value,
                    "source": doc.get("source", "unknown"),
                }
            )
        return results

    async def resolve_placeholders(
        self, *, session_id: str, collection_id: str, placeholders: set[str]
    ) -> dict[str, str]:
        """Batch placeholder -> real value lookup, for detokenizing a response."""
        if not placeholders:
            return {}
        try:
            cursor = self._mappings.find(
                {
                    "session_id": session_id,
                    "collection_id": collection_id,
                    "placeholder": {"$in": sorted(placeholders)},
                }
            )
            docs = await cursor.to_list(length=len(placeholders))
        except Exception as exc:
            raise EntityVaultUnavailableError(str(exc)) from exc
        result: dict[str, str] = {}
        for doc in docs:
            try:
                result[doc["placeholder"]] = _unseal(
                    doc,
                    session_id=session_id,
                    collection_id=collection_id,
                    placeholder=doc["placeholder"],
                )
            except Exception as exc:
                raise VaultKeyMisconfiguredError(
                    f"failed to decrypt mapping for {doc['placeholder']!r}: {exc}"
                ) from exc
        return result

    async def delete_entity(
        self, *, session_id: str, collection_id: str, entity_type: str, value: str
    ) -> bool:
        """Delete the entity.

        Args:
            session_id (str): Session scope the record belongs to.
            collection_id (str): Knowledge collection identifier.
            entity_type (str): The entity type.
            value (str): Value to process.

        Returns:
            bool: The entity.
        """
        ehash = entity_hash(entity_type, value)
        _id = _mapping_id(session_id, collection_id, ehash)
        try:
            result = await self._mappings.delete_one({"_id": _id})
        except Exception as exc:
            raise EntityVaultUnavailableError(str(exc)) from exc
        return bool(result.deleted_count)


async def ensure_indexes(db: Any) -> None:
    """Module-level convenience matching this codebase's ``ensure_*_indexes`` convention."""
    await EntityVault(db).ensure_indexes()
