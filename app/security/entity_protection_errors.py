"""Fail-closed exception hierarchy for confidential entity protection.

Every one of these is raised BEFORE any external LLM/embedding call is made
(see the interception wrapper in app/llm/registry.py) — the invariant this
whole hierarchy exists to protect is that no failure mode here can result in
a network call proceeding with unprotected text. None of these are ever
swallowed silently; callers let them propagate so the workflow run is marked
failed rather than silently succeeding with plaintext sent externally.
"""
from __future__ import annotations


class EntityProtectionError(Exception):
    """Base class for every fail-closed entity-protection error."""


class EntityVaultUnavailableError(EntityProtectionError):
    """The Mongo-backed mapping vault could not be reached (down/timeout)."""


class EntityScanUnavailableError(EntityProtectionError):
    """The spaCy NER safety-net pass crashed or could not run."""


class VaultKeyMisconfiguredError(EntityProtectionError):
    """entity_vault_master_key is missing, a placeholder, or too short."""


class UnresolvedEntityRiskError(EntityProtectionError):
    """A residual entity candidate could not be confidently tokenized."""


class ResponseLeakDetectedError(EntityProtectionError):
    """A registered real entity value appeared verbatim in a model response."""
