"""Provider-neutral LLM errors used by the resilience layer."""

from __future__ import annotations


class StructuredOutputError(RuntimeError):
    """The provider responded, but its output violated the required schema.

    This error is retryable because another generation or fallback model
    may return valid structured output.
    """


class LLMInputLimitError(ValueError):
    """The resolved prompt exceeds a configured token/cost safety boundary."""


class LLMProviderUnavailableError(RuntimeError):
    """No configured provider can serve the requested model/fallback chain."""
