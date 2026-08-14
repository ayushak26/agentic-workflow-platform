"""Tests for app/llm/openrouter_gw.py's structured-output parsing.

The fenced payloads below are the real thing, captured from a live OpenRouter sweep on
2026-08-14 (scripts/openrouter_health_check.py). Three Anthropic models fenced their
JSON while Opus 5 and Sonnet 5 did not, so unfencing has to be a recovery path that
leaves the well-behaved majority untouched.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app.llm.openrouter_gw import _parse_structured, unfence_json


class HealthReport(BaseModel):
    status: str
    number: int


# Verbatim from claude-sonnet-4.6 and claude-haiku-4.5 respectively.
FENCED_SINGLE_LINE = '```json\n{"status": "ok", "number": 7}\n```'
FENCED_PRETTY = '```json\n{\n  "status": "ok",\n  "number": 7\n}\n```'
BARE = '{"status": "ok", "number": 7}'


@pytest.mark.parametrize(
    "payload",
    [
        FENCED_SINGLE_LINE,
        FENCED_PRETTY,
        '```\n{"status": "ok", "number": 7}\n```',  # no language tag
        '```JSON\n{"status": "ok", "number": 7}\n```',  # upper-case tag
        '  ```json\n{"status": "ok", "number": 7}\n```  ',  # surrounding whitespace
    ],
)
def test_fenced_payloads_parse(payload: str) -> None:
    parsed = _parse_structured(payload, HealthReport)
    assert (parsed.status, parsed.number) == ("ok", 7)


def test_unfenced_payload_is_untouched() -> None:
    """The common case must not route through the recovery path at all."""
    assert unfence_json(BARE) == BARE
    parsed = _parse_structured(BARE, HealthReport)
    assert (parsed.status, parsed.number) == ("ok", 7)


def test_prose_wrapped_json_is_not_unwrapped() -> None:
    """Only whole-string fences are unwrapped — guessing at a JSON span inside prose
    could silently parse the wrong object, so this stays a loud failure."""
    payload = 'Here you go:\n```json\n{"status": "ok", "number": 7}\n```\nHope that helps!'
    assert unfence_json(payload) == payload
    with pytest.raises(ValidationError):
        _parse_structured(payload, HealthReport)


def test_fenced_but_invalid_json_still_raises() -> None:
    with pytest.raises(ValidationError):
        _parse_structured('```json\n{"status": "ok"}\n```', HealthReport)  # missing field


def test_json_containing_backticks_survives_roundtrip() -> None:
    """A fenced body whose own content has backticks must not be truncated early."""
    payload = '```json\n{"status": "use ``code`` here", "number": 7}\n```'
    parsed = _parse_structured(payload, HealthReport)
    assert parsed.status == "use ``code`` here"
