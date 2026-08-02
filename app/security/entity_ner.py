"""Lazy-loaded spaCy NER safety net — PERSON/ORG spans not already known.

The registry (app/security/entity_registry.py) is the primary, reliable
detection mechanism; this module only catches unregistered names in free
text (e.g. a person's name inside an uploaded document). English-only
(en_core_web_sm) — a known, accepted gap for multilingual consortium names,
which the registry layer must cover instead.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from app.observability.logging import get_logger
from app.security.entity_protection_errors import EntityScanUnavailableError

log = get_logger(__name__)

_SPACY_MODEL = "en_core_web_sm"
_LABEL_TO_ENTITY_TYPE = {"ORG": "organisation", "PERSON": "person"}

_pipeline = None
_pipeline_lock = threading.Lock()


@dataclass(frozen=True)
class EntityMatch:
    start: int
    end: int
    text: str
    entity_type: str


def _load_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        try:
            import spacy

            _pipeline = spacy.load(_SPACY_MODEL, disable=["lemmatizer", "parser"])
            log.info("entity_ner.pipeline_loaded", model=_SPACY_MODEL)
        except Exception as exc:
            log.error("entity_ner.load_failed", error=str(exc))
            raise EntityScanUnavailableError(
                f"spaCy model {_SPACY_MODEL!r} unavailable: {exc}"
            ) from exc
    return _pipeline


def extract_entities(text: str) -> list[EntityMatch]:
    """PERSON/ORG spans via spaCy. Synchronous/CPU-bound — callers on the
    async path should run this via ``asyncio.to_thread`` to avoid blocking
    the event loop (see app/security/entity_tokenizer.py).

    Raises EntityScanUnavailableError if spaCy can't run at all — callers
    must fail closed (block the call), never silently skip this layer.
    """
    if not text:
        return []
    pipeline = _load_pipeline()
    try:
        doc = pipeline(text)
    except Exception as exc:
        raise EntityScanUnavailableError(f"spaCy inference failed: {exc}") from exc

    matches: list[EntityMatch] = []
    for ent in doc.ents:
        entity_type = _LABEL_TO_ENTITY_TYPE.get(ent.label_)
        if entity_type is None:
            continue
        matches.append(
            EntityMatch(
                start=ent.start_char,
                end=ent.end_char,
                text=ent.text,
                entity_type=entity_type,
            )
        )
    return matches
