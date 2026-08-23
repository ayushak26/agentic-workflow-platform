"""Deterministic input/output guardrails for workflow execution.

The platform handles proposals and evidence documents, so PII cannot simply be
rejected in every case. The configured PII mode is therefore explicit:
``audit`` records only finding types, ``redact`` replaces values before model
use, and ``block`` rejects the request. High-confidence prompt-injection
phrases in direct user inputs are always blocked. Retrieved documents remain
untrusted context and are delimited by the RAG nodes rather than scanned as
instructions.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.observability import metrics
from app.observability.logging import get_logger

log = get_logger(__name__)


class GuardrailViolation(ValueError):
    """Raised when a request or generated output violates a hard guardrail."""


@dataclass(frozen=True)
class GuardrailFinding:
    """Provides the GuardrailFinding behaviour.

    Attributes:
        kind (str).
        path (str).
    """
    kind: str
    path: str


@dataclass
class GuardrailResult:
    """Provides the GuardrailResult behaviour.

    Attributes:
        value (Any).
        findings (list[GuardrailFinding]).
    """
    value: Any
    findings: list[GuardrailFinding] = field(default_factory=list)


_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b", re.I),
    re.compile(r"\b(reveal|print|show|return)\s+(the\s+)?system\s+prompt\b", re.I),
    re.compile(r"\bact\s+as\s+(dan|developer\s+mode)\b", re.I),
    re.compile(r"<\|(?:system|assistant|developer)\|>", re.I),
    re.compile(r"\bdisable\s+(all\s+)?(safety|guardrails?|security)\b", re.I),
)

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    ),
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    ),
    (
        "us_ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "international_phone",
        re.compile(r"(?<!\w)\+\d[\d ()-]{7,}\d"),
    ),
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9_./+=-]{16,}"
        ),
    ),
)


def _redact_matches(
    text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    path: str,
) -> tuple[str, list[GuardrailFinding]]:
    """Redact the matches.

    Args:
        text (str): The text.
        patterns (tuple[tuple[str, re.Pattern[str]], ...]): The patterns.
        path (str): Filesystem path.

    Returns:
        tuple[str, list[GuardrailFinding]]: The matches.
    """
    findings: list[GuardrailFinding] = []
    updated = text
    for kind, pattern in patterns:
        if pattern.search(updated):
            findings.append(GuardrailFinding(kind=kind, path=path))
            updated = pattern.sub(f"[REDACTED_{kind.upper()}]", updated)
    return updated, findings


def _walk(
    value: Any,
    *,
    path: str,
    transform,
) -> tuple[Any, list[GuardrailFinding]]:
    """Internal helper for the walk step.

    Args:
        value (Any): Value to process.
        path (str): Filesystem path.
        transform: The transform.

    Returns:
        tuple[Any, list[GuardrailFinding]]: The result.
    """
    if isinstance(value, str):
        return transform(value, path)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        findings: list[GuardrailFinding] = []
        for key, item in value.items():
            child, child_findings = _walk(
                item,
                path=f"{path}.{key}",
                transform=transform,
            )
            out[str(key)] = child
            findings.extend(child_findings)
        return out, findings
    if isinstance(value, list):
        out_list: list[Any] = []
        findings = []
        for index, item in enumerate(value):
            child, child_findings = _walk(
                item,
                path=f"{path}[{index}]",
                transform=transform,
            )
            out_list.append(child)
            findings.extend(child_findings)
        return out_list, findings
    return deepcopy(value), []


def check_workflow_inputs(value: Any) -> GuardrailResult:
    """Block injection and apply the configured PII policy to direct inputs."""

    if not settings.guardrails_enabled:
        return GuardrailResult(value=value)

    def transform(text: str, path: str) -> tuple[str, list[GuardrailFinding]]:
        """Compute the transform.

        Args:
            text (str): The text.
            path (str): Filesystem path.

        Returns:
            tuple[str, list[GuardrailFinding]]: The result.
        """
        if len(text) > settings.guardrail_max_text_chars:
            raise GuardrailViolation(
                f"Text input at {path} exceeds the "
                f"{settings.guardrail_max_text_chars:,}-character safety limit"
            )
        if any(pattern.search(text) for pattern in _INJECTION_PATTERNS):
            metrics.GUARDRAIL_EVENTS.labels(
                direction="input",
                outcome="blocked",
            ).inc()
            log.warning("guardrail.input_blocked", reason="prompt_injection", path=path)
            raise GuardrailViolation(
                f"Potential prompt injection detected in input {path}"
            )

        redacted, findings = _redact_matches(text, _PII_PATTERNS, path=path)
        if findings and settings.guardrail_pii_mode == "block":
            kinds = sorted({finding.kind for finding in findings})
            raise GuardrailViolation(
                f"PII is not allowed in input {path}: {', '.join(kinds)}"
            )
        if settings.guardrail_pii_mode == "redact":
            return redacted, findings
        return text, findings

    guarded, findings = _walk(value, path="inputs", transform=transform)
    if findings:
        metrics.GUARDRAIL_EVENTS.labels(
            direction="input",
            outcome=settings.guardrail_pii_mode,
        ).inc()
        log.info(
            "guardrail.input_findings",
            finding_types=sorted({finding.kind for finding in findings}),
            finding_count=len(findings),
        )
    return GuardrailResult(value=guarded, findings=findings)


def check_generated_output(value: Any) -> GuardrailResult:
    """Remove credentials/private keys from model and tool output."""

    if not settings.guardrails_enabled:
        return GuardrailResult(value=value)

    def transform(text: str, path: str) -> tuple[str, list[GuardrailFinding]]:
        """Compute the transform.

        Args:
            text (str): The text.
            path (str): Filesystem path.

        Returns:
            tuple[str, list[GuardrailFinding]]: The result.
        """
        if len(text) > settings.guardrail_max_text_chars:
            raise GuardrailViolation(
                f"Generated text at {path} exceeds the output safety limit"
            )
        return _redact_matches(text, _SECRET_PATTERNS, path=path)

    guarded, findings = _walk(value, path="output", transform=transform)
    if findings:
        metrics.GUARDRAIL_EVENTS.labels(
            direction="output",
            outcome="redacted",
        ).inc()
        log.warning(
            "guardrail.output_redacted",
            finding_types=sorted({finding.kind for finding in findings}),
            finding_count=len(findings),
        )
    return GuardrailResult(value=guarded, findings=findings)
