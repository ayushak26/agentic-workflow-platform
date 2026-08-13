"""Opaque, sortable Knowledge Studio resource identifiers.

Identifiers are ULID-shaped: a 48-bit millisecond timestamp followed by 80
bits of randomness, Crockford base32 encoded, behind a short kind prefix
(``col_01J0…``).  That keeps them opaque to the product surface while still
sorting chronologically inside Mongo indexes.

Legacy identifiers are never rewritten.  ``scoped_legacy_id`` derives a
deterministic identifier for records that predate this scheme, so a backfill
can run repeatedly without minting a second identifier for the same thing.
"""
from __future__ import annotations

import hashlib
import os
import time

# Crockford base32: no I, L, O or U, so identifiers survive transcription.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_TIMESTAMP_CHARS = 10
_RANDOM_CHARS = 16

#: Resource kind -> identifier prefix. Prefixes are part of the stored data,
#: so entries may be added but must never be renamed.
ID_PREFIXES: dict[str, str] = {
    "workspace": "ws",
    "collection": "col",
    "source": "src",
    "document": "doc",
    "source_version": "srcver",
    "ingestion_job": "ing",
    "parser_profile": "parserprof",
    "chunking_profile": "chkprof",
    "embedding_profile": "embprof",
    "index": "idx",
    "retrieval_profile": "retprof",
    "reranker_profile": "rerankprof",
    "generation_profile": "genprof",
    "routing_profile": "routeprof",
    "rag_agent": "rag",
    "retrieval_request": "retreq",
    "chunk": "chk",
}

_PREFIX_TO_KIND = {prefix: kind for kind, prefix in ID_PREFIXES.items()}


def _encode(value: int, length: int) -> str:
    out = ["0"] * length
    for position in range(length - 1, -1, -1):
        out[position] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


def prefix_for(kind: str) -> str:
    try:
        return ID_PREFIXES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown resource kind {kind!r}") from exc


def new_resource_id(kind: str) -> str:
    """Mint a fresh opaque identifier for ``kind``."""

    prefix = prefix_for(kind)
    timestamp = _encode(int(time.time() * 1000) & ((1 << 48) - 1), _TIMESTAMP_CHARS)
    randomness = _encode(int.from_bytes(os.urandom(10), "big"), _RANDOM_CHARS)
    return f"{prefix}_{timestamp}{randomness}"


def scoped_legacy_id(kind: str, owner_scope_id: str, legacy_key: str) -> str:
    """Derive a stable identifier for a pre-existing record.

    The same ``(kind, owner_scope_id, legacy_key)`` always yields the same
    identifier, which is what makes the legacy backfill idempotent.  The
    digest also keeps raw scope names and filenames out of the identifier.
    """

    prefix = prefix_for(kind)
    digest = hashlib.sha256(
        f"{kind}\x00{owner_scope_id}\x00{legacy_key}".encode()
    ).digest()
    body = _encode(
        int.from_bytes(digest[:16], "big"), _TIMESTAMP_CHARS + _RANDOM_CHARS
    )
    return f"{prefix}_{body}"


def kind_of(resource_id: str) -> str | None:
    """Return the resource kind for ``resource_id``, or ``None`` if unknown.

    Legacy identifiers that carry no recognised prefix return ``None`` rather
    than raising, so compatibility paths can fall back instead of failing.
    """

    prefix, separator, _ = resource_id.partition("_")
    if not separator:
        return None
    return _PREFIX_TO_KIND.get(prefix)


def is_kind(resource_id: str, kind: str) -> bool:
    return kind_of(resource_id) == kind
