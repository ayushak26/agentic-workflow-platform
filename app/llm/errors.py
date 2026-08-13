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


class BatchTimeoutError(RuntimeError):
    """A submitted batch did not reach a terminal state within the deadline.

    Not retryable in the usual sense -- the batch may still finish later.
    Callers should re-poll with the same batch_id rather than resubmitting.
    """


class LLMPolicyDeniedError(RuntimeError):
    """The data-classification/OPA policy check (app/security/llm_policy.py) denied this
    call — e.g. CONFIDENTIAL+ data routed to a provider without a confirmed ZDR contract.
    Not retryable: the same call will be denied again until the workflow's data
    classification or target provider changes."""
