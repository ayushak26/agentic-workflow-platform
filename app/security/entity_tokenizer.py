"""Entity tokenization orchestration — the Phase 1 core guarantee.

``tokenize()`` replaces every registry/regex/NER-detected protected entity in
a string with a stable placeholder before it reaches an external LLM call.
``detokenize()`` reverses it on the response, validates for tampered/
hallucinated placeholders, and rescans for verbatim entity leaks.

Detection order (longest-match-wins on overlap): registry literal scan (the
primary, reliable mechanism) -> regex safety net (email/phone reused from
app.security.guardrails, plus domain/grant-agreement-number patterns here)
-> spaCy NER safety net (PERSON/ORG spans not already claimed). See
app/security/entity_ner.py and app/security/entity_registry.py.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.observability import metrics
from app.observability.logging import get_logger
from app.security.entity_ner import extract_entities
from app.security.entity_protection_errors import ResponseLeakDetectedError
from app.security.entity_registry import EntityRegistry
from app.security.entity_vault import EntityVault
from app.security.guardrails import _PII_PATTERNS

log = get_logger(__name__)

_PLACEHOLDER_PATTERN = re.compile(r"\[\[ENTITY_[A-Z_]+_\d+\]\]")

# Only email/phone are reused from guardrails — credit_card/us_ssn are a
# different (non-entity) protection concern, out of scope here.
_REUSED_PII_ENTITY_TYPES = {"email": "email", "international_phone": "phone"}

_DOMAIN_PATTERN = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:com|org|net|edu|gov|eu|io|de|fr|it|es|nl|uk|co)\b",
    re.I,
)
_GRANT_AGREEMENT_PATTERN = re.compile(
    r"\bGrant\s+Agreement\s+(?:No\.?|Number)?\s*[:#]?\s*(\d{6,9})\b", re.I
)


class ProcessingMode(str, Enum):
    PUBLIC = "public"
    PSEUDONYMISED = "pseudonymised"
    RESTRICTED_LOCAL = "restricted_local"


@dataclass
class TokenizeResult:
    text: str
    placeholders_used: frozenset[str] = field(default_factory=frozenset)


@dataclass
class DetokenizeResult:
    value: Any
    unresolved_placeholders: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    entity_type: str
    value: str
    source: str  # "manual" (registry hit — the entity already existed) | "auto_detected"


def _overlaps(start: int, end: int, claimed: list[tuple[int, int]]) -> bool:
    return any(not (end <= c_start or start >= c_end) for c_start, c_end in claimed)


def _boundary_pattern(value: str) -> re.Pattern[str]:
    escaped = re.escape(value)
    prefix = r"\b" if value[:1].isalnum() else ""
    suffix = r"\b" if value[-1:].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.I)


class EntityTokenizerService:
    def __init__(self, db: Any) -> None:
        self._vault = EntityVault(db)
        self._registry = EntityRegistry(self._vault)

    @property
    def registry(self) -> EntityRegistry:
        """Exposed for the manual-registration API and proposal-graph sync."""
        return self._registry

    async def ensure_indexes(self) -> None:
        await self._vault.ensure_indexes()

    async def tokenize(
        self,
        text: str,
        *,
        session_id: str,
        collection_id: str = "default",
        mode: ProcessingMode = ProcessingMode.PSEUDONYMISED,
    ) -> TokenizeResult:
        if mode is ProcessingMode.PUBLIC or not text:
            return TokenizeResult(text=text)
        if mode is ProcessingMode.RESTRICTED_LOCAL:
            # Not implemented yet — fail toward *more* protection, never
            # silently less, if a workflow prematurely selects this mode.
            log.warning(
                "entity_tokenizer.restricted_local_not_implemented",
                session_id=session_id,
            )
            mode = ProcessingMode.PSEUDONYMISED

        spans = await self._detect_spans(
            text, session_id=session_id, collection_id=collection_id
        )
        if not spans:
            return TokenizeResult(text=text)

        # Right-to-left so earlier offsets stay valid as we splice.
        spans_sorted = sorted(spans, key=lambda s: s.start, reverse=True)
        rewritten = text
        placeholders_used: set[str] = set()
        for span in spans_sorted:
            placeholder = await self._vault.get_or_create_placeholder(
                session_id=session_id,
                collection_id=collection_id,
                entity_type=span.entity_type,
                real_value=span.value,
                source=span.source,
            )
            placeholders_used.add(placeholder)
            rewritten = rewritten[: span.start] + placeholder + rewritten[span.end :]
        return TokenizeResult(
            text=rewritten, placeholders_used=frozenset(placeholders_used)
        )

    async def _detect_spans(
        self, text: str, *, session_id: str, collection_id: str
    ) -> list[_Span]:
        known = await self._vault.list_scope_entities(
            session_id=session_id, collection_id=collection_id
        )
        claimed: list[tuple[int, int]] = []
        spans: list[_Span] = []

        # 1. Registry — longest known value first; this must beat any
        # NER/regex hit on an overlapping span.
        for entry in sorted(known, key=lambda e: len(e["value"]), reverse=True):
            value = entry["value"]
            if not value:
                continue
            for match in _boundary_pattern(value).finditer(text):
                if _overlaps(match.start(), match.end(), claimed):
                    continue
                claimed.append((match.start(), match.end()))
                spans.append(
                    _Span(
                        match.start(),
                        match.end(),
                        entry["entity_type"],
                        match.group(0),
                        "manual",
                    )
                )

        # 2. Regex safety net.
        for kind, pattern in _PII_PATTERNS:
            entity_type = _REUSED_PII_ENTITY_TYPES.get(kind)
            if entity_type is None:
                continue
            for match in pattern.finditer(text):
                if _overlaps(match.start(), match.end(), claimed):
                    continue
                claimed.append((match.start(), match.end()))
                spans.append(
                    _Span(
                        match.start(), match.end(), entity_type, match.group(0),
                        "auto_detected",
                    )
                )
        for match in _DOMAIN_PATTERN.finditer(text):
            if _overlaps(match.start(), match.end(), claimed):
                continue
            claimed.append((match.start(), match.end()))
            spans.append(
                _Span(
                    match.start(), match.end(), "domain", match.group(0),
                    "auto_detected",
                )
            )
        for match in _GRANT_AGREEMENT_PATTERN.finditer(text):
            start, end = match.span(1)
            if _overlaps(start, end, claimed):
                continue
            claimed.append((start, end))
            spans.append(
                _Span(
                    start, end, "grant_agreement_number", match.group(1),
                    "auto_detected",
                )
            )

        # 3. spaCy NER safety net over whatever's still unclaimed. CPU-bound —
        # run off the event loop. Raises EntityScanUnavailableError if spaCy
        # can't run; that propagates and blocks the call (fail closed).
        ner_matches = await asyncio.to_thread(extract_entities, text)
        for ner_match in ner_matches:
            if _overlaps(ner_match.start, ner_match.end, claimed):
                continue
            claimed.append((ner_match.start, ner_match.end))
            spans.append(
                _Span(
                    ner_match.start, ner_match.end, ner_match.entity_type,
                    ner_match.text, "auto_detected",
                )
            )

        return spans

    async def detokenize(
        self,
        value: Any,
        *,
        session_id: str,
        collection_id: str = "default",
    ) -> DetokenizeResult:
        """Recursively walks str/dict/list, restoring placeholders in every
        leaf string. Other leaf types pass through unchanged."""
        if isinstance(value, str):
            return await self._detokenize_text(
                value, session_id=session_id, collection_id=collection_id
            )
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            unresolved: set[str] = set()
            for key, item in value.items():
                sub = await self.detokenize(
                    item, session_id=session_id, collection_id=collection_id
                )
                result[key] = sub.value
                unresolved |= sub.unresolved_placeholders
            return DetokenizeResult(
                value=result, unresolved_placeholders=frozenset(unresolved)
            )
        if isinstance(value, list):
            items: list[Any] = []
            unresolved = set()
            for item in value:
                sub = await self.detokenize(
                    item, session_id=session_id, collection_id=collection_id
                )
                items.append(sub.value)
                unresolved |= sub.unresolved_placeholders
            return DetokenizeResult(
                value=items, unresolved_placeholders=frozenset(unresolved)
            )
        return DetokenizeResult(value=value)

    async def _detokenize_text(
        self, text: str, *, session_id: str, collection_id: str
    ) -> DetokenizeResult:
        found = set(_PLACEHOLDER_PATTERN.findall(text))
        resolved: dict[str, str] = {}
        if found:
            resolved = await self._vault.resolve_placeholders(
                session_id=session_id,
                collection_id=collection_id,
                placeholders=found,
            )
        unresolved = found - set(resolved.keys())
        if unresolved:
            # A placeholder-shaped token the model emitted that isn't in
            # this call's mapping scope — hallucinated or altered. Don't
            # guess a replacement; leave it as-is and surface it.
            metrics.ENTITY_TOKENIZER_EVENTS.labels(
                outcome="unresolved_placeholder"
            ).inc()
            log.warning(
                "entity_tokenizer.unresolved_placeholder",
                placeholders=sorted(unresolved),
                session_id=session_id,
            )

        # Verbatim-leak rescan on the RAW response text (before we splice
        # anything back in) — any registered real value appearing here means
        # the model itself wrote it in plaintext (e.g. echoed from retrieved
        # content), independent of our own substitution below.
        known = await self._vault.list_scope_entities(
            session_id=session_id, collection_id=collection_id
        )
        for entry in known:
            candidate = entry["value"]
            if candidate and _boundary_pattern(candidate).search(text):
                metrics.ENTITY_TOKENIZER_EVENTS.labels(
                    outcome="response_leak_detected"
                ).inc()
                log.error(
                    "entity_tokenizer.response_leak_detected",
                    entity_type=entry["entity_type"],
                    session_id=session_id,
                )
                raise ResponseLeakDetectedError(
                    f"a registered {entry['entity_type']} value appeared "
                    "verbatim in a model response"
                )

        rewritten = text
        for placeholder, real_value in resolved.items():
            rewritten = rewritten.replace(placeholder, real_value)
        return DetokenizeResult(
            value=rewritten, unresolved_placeholders=frozenset(unresolved)
        )
