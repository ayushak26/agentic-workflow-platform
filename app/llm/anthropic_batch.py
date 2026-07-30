"""Anthropic Message Batches API -- standalone, opt-in batch service.

50% cheaper than synchronous requests; results land within an hour for most
batches, up to 24h max. Not wired into any existing call site -- this is a
reusable building block for a caller (e.g. a fan-out of independent
complete_structured() calls) to opt into when it can accept delayed results.

Requests still get cache_control breakpoints (reusing the same
_cacheable_system helper as AnthropicGateway), so a batch of otherwise
identical requests that share a system prompt/tools also gets cache-read
pricing on later items in the same batch, on top of the batch discount.
"""
from __future__ import annotations

from typing import Type

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm.anthropic_gw import _cacheable_system, _supports_temperature
from app.llm.batch_types import BatchRequestItem, BatchResultItem, BatchStatus

# Anthropic's own processing_status values; "ended" is the only terminal one.
_ENDED_STATUS = "ended"


def _tool_name_for(response_model: Type[BaseModel]) -> str:
    return "emit_" + response_model.__name__.lower()


def _build_params(item: BatchRequestItem) -> dict:
    """Build one MessageCreateParamsNonStreaming-shaped dict for a batch row."""
    params: dict = {
        "model": item.model,
        "system": _cacheable_system(item.system),
        "messages": [{"role": "user", "content": item.user}],
        "max_tokens": item.max_tokens,
    }
    if item.temperature is not None and _supports_temperature(item.model):
        params["temperature"] = item.temperature
    if item.response_model is not None:
        tool_name = _tool_name_for(item.response_model)
        params["tools"] = [{
            "name": tool_name,
            "description": f"Emit a well-formed {item.response_model.__name__}.",
            "input_schema": item.response_model.model_json_schema(),
        }]
        params["tool_choice"] = {"type": "tool", "name": tool_name}
    return params


class AnthropicBatchService:
    """Submit, poll, and read back an Anthropic Message Batch."""

    def __init__(self, api_key: str | None = None):
        self._client = AsyncAnthropic(
            api_key=api_key or settings.anthropic_api_key,
            timeout=settings.llm_request_timeout_seconds,
            max_retries=0,
        )

    async def submit(self, items: list[BatchRequestItem]) -> str:
        """Submit a batch and return its batch_id. Does not wait for results."""
        requests = [
            {"custom_id": item.custom_id, "params": _build_params(item)}
            for item in items
        ]
        batch = await self._client.messages.batches.create(requests=requests)
        return batch.id

    async def status(self, batch_id: str) -> BatchStatus:
        batch = await self._client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        return BatchStatus(
            batch_id=batch.id,
            processing_status=batch.processing_status,
            request_counts={
                "processing": counts.processing,
                "succeeded": counts.succeeded,
                "errored": counts.errored,
                "canceled": counts.canceled,
                "expired": counts.expired,
            },
            ended=batch.processing_status == _ENDED_STATUS,
        )

    async def cancel(self, batch_id: str) -> str:
        """Request cancellation. Already-completed rows still return results."""
        batch = await self._client.messages.batches.cancel(batch_id)
        return batch.processing_status

    async def results(
        self,
        batch_id: str,
        *,
        response_models: dict[str, Type[BaseModel]] | None = None,
    ) -> list[BatchResultItem]:
        """Read back results for an ended batch.

        `response_models` maps custom_id -> the Pydantic model that
        request's item.response_model was set to, so structured rows can be
        parsed and validated the same way complete_structured() does. Rows
        submitted without a response_model (or omitted from this dict) come
        back as plain text in `.text`.
        """
        response_models = response_models or {}
        out: list[BatchResultItem] = []
        decoder = await self._client.messages.batches.results(batch_id)
        async for result in decoder:
            custom_id = result.custom_id
            rtype = result.result.type

            if rtype != "succeeded":
                error_message = None
                if rtype == "errored":
                    # result.result.error is an ErrorResponse; the message
                    # lives one level down on its nested .error object.
                    inner = getattr(result.result.error, "error", None)
                    error_message = getattr(inner, "message", None) or str(result.result.error)
                out.append(BatchResultItem(
                    custom_id=custom_id,
                    status=rtype,
                    error=error_message,
                ))
                continue

            msg = result.result.message
            text_parts = [b.text for b in msg.content if b.type == "text"]
            parsed = None
            response_model = response_models.get(custom_id)
            if response_model is not None:
                tool_name = _tool_name_for(response_model)
                tool_block = next(
                    (b for b in msg.content if b.type == "tool_use" and b.name == tool_name),
                    None,
                )
                if tool_block is not None:
                    try:
                        parsed = response_model.model_validate(tool_block.input)
                    except ValidationError:
                        parsed = None  # caller can inspect .text/.error and retry
            out.append(BatchResultItem(
                custom_id=custom_id,
                status="succeeded",
                text="".join(text_parts) if text_parts else None,
                parsed=parsed,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
                cache_creation_input_tokens=getattr(
                    msg.usage, "cache_creation_input_tokens", 0
                ) or 0,
                cache_read_input_tokens=getattr(
                    msg.usage, "cache_read_input_tokens", 0
                ) or 0,
                model=msg.model,
            ))
        return out
