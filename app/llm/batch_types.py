"""Provider-neutral shapes for the Anthropic/OpenAI Batch API services.

Standalone from LLMGateway on purpose: batch results land minutes-to-24h
after submission, which doesn't fit the synchronous complete()/
complete_structured()/chat_with_tools() contract. Callers that want batch
semantics (see app.llm.anthropic_batch / app.llm.openai_batch) submit a list
of BatchRequestItem, poll, then read back a list of BatchResultItem.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

BatchResultStatus = Literal["succeeded", "errored", "canceled", "expired"]


@dataclass
class BatchRequestItem:
    """One request in a batch submission.

    Mirrors the params of LLMGateway.complete() / complete_structured() so a
    caller migrating an existing fan-out (e.g. asyncio.gather over N
    complete_structured calls) can build these directly from what it already
    passes today. Set response_model to force structured output the same
    way complete_structured() does (tool-use on Anthropic, JSON mode on
    OpenAI); leave it unset for a plain-text completion.
    """
    custom_id: str
    model: str
    system: str = ""
    user: str = ""
    max_tokens: int = 4096
    temperature: float | None = None
    response_model: Type[BaseModel] | None = None


@dataclass
class BatchResultItem:
    """One result row, keyed by the custom_id it was submitted with.

    Results arrive in arbitrary order from both providers -- always match by
    custom_id, never by position.
    """
    custom_id: str
    status: BatchResultStatus
    text: str | None = None
    parsed: BaseModel | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    model: str | None = None
    error: str | None = None


@dataclass
class BatchStatus:
    """Point-in-time status of a submitted batch."""
    batch_id: str
    processing_status: str   # raw provider status string (e.g. "in_progress")
    request_counts: dict[str, int]
    ended: bool               # True once no more results will arrive
