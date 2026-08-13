"""Presidio Analyzer client — the PII/entity detection safety-net tier.

Replaces the former in-process spaCy NER pass (app/security/entity_ner.py, removed):
Presidio's own analyzer already runs NER (PERSON/NRP/LOCATION) plus a wide set of built-in
pattern recognizers (EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, IBAN_CODE, IP_ADDRESS, US_SSN,
CRYPTO, and more) behind one HTTP call, so there is no reason to hand-maintain regex/NER code
for anything Presidio already covers. Eurskem-specific patterns Presidio has no knowledge of
(domain names, grant-agreement numbers) stay as dedicated regexes in entity_tokenizer.py.

Fail-closed, matching the rest of the entity-protection module: an unreachable or erroring
Presidio raises EntityScanUnavailableError rather than silently skipping detection.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings
from app.security.entity_protection_errors import EntityScanUnavailableError

# Presidio's own entity-type names -> Eurskem's placeholder-friendly entity_type strings.
# Anything not listed here falls back to a lowercased version of Presidio's own type name
# (e.g. "CREDIT_CARD" -> "credit_card") rather than being dropped — an unmapped Presidio
# recognizer still means "this is sensitive", so it must still be tokenized.
_ENTITY_TYPE_MAP: dict[str, str] = {
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "PERSON": "person",
    "ORGANIZATION": "organisation",
    "NRP": "organisation",  # nationality/religious/political group — same protection tier
}


@dataclass(frozen=True)
class PresidioMatch:
    start: int
    end: int
    text: str
    entity_type: str
    score: float


def _map_entity_type(presidio_type: str) -> str:
    return _ENTITY_TYPE_MAP.get(presidio_type, presidio_type.lower())


async def analyze(
    text: str,
    *,
    language: str = "en",
    score_threshold: float = 0.5,
    client: httpx.AsyncClient | None = None,
) -> list[PresidioMatch]:
    """Calls POST {presidio_analyzer_url}/analyze. Raises EntityScanUnavailableError on any
    failure (unreachable, timeout, non-2xx, malformed response) — callers must fail closed,
    never silently skip this detection layer."""
    if not text:
        return []

    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        base_url=settings.presidio_analyzer_url,
        timeout=settings.presidio_request_timeout_seconds,
    )
    try:
        try:
            response = await active_client.post(
                "/analyze",
                json={"text": text, "language": language, "score_threshold": score_threshold},
            )
            response.raise_for_status()
            results = response.json()
        except httpx.HTTPError as exc:
            raise EntityScanUnavailableError(f"Presidio request failed: {exc}") from exc
        except ValueError as exc:
            raise EntityScanUnavailableError(f"Presidio response was not valid JSON: {exc}") from exc
    finally:
        if owns_client:
            await active_client.aclose()

    if not isinstance(results, list):
        raise EntityScanUnavailableError("Presidio response was not a JSON array")

    matches: list[PresidioMatch] = []
    for item in results:
        try:
            start, end = int(item["start"]), int(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EntityScanUnavailableError(f"Presidio result missing start/end: {exc}") from exc
        matches.append(
            PresidioMatch(
                start=start,
                end=end,
                text=text[start:end],
                entity_type=_map_entity_type(item.get("entity_type", "")),
                score=float(item.get("score", 0.0)),
            )
        )
    return matches
