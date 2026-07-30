"""OpenAI Batch API -- standalone, opt-in batch service.

50% cheaper than synchronous requests; most batches complete well inside
the 24h completion window. Not wired into any existing call site -- this is
a reusable building block for a caller that can accept delayed results.

Flow: upload a JSONL request file -> create a batch against it -> poll
status -> download the output (and, for partial failures, error) file and
parse each JSONL row back into a BatchResultItem.
"""
from __future__ import annotations

import json
from typing import Type

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm.batch_types import BatchRequestItem, BatchResultItem, BatchStatus
from app.llm.openai_gw import _completion_tokens_for, _supports_custom_temperature, _system_messages

# OpenAI's own batch.status values. Every one of these ends the batch --
# "completed" with results, the rest with partial-or-no output.
_ENDED_STATUSES = {"completed", "expired", "cancelled", "failed"}

_STRUCTURED_OUTPUT_INSTRUCTIONS = (
    "\n\nRespond with a single JSON object that conforms to this schema. "
    "Return every field using its native JSON type (objects as JSON "
    "objects, lists as JSON arrays). Output only the JSON, no prose.\n"
    "SCHEMA:\n{schema}"
)


def _build_body(item: BatchRequestItem) -> dict:
    """Build one /v1/chat/completions request body for a batch JSONL row.

    Mirrors OpenAIGateway.complete()/complete_structured(): manual JSON-mode
    structured output (schema folded into the system prompt), not .parse(),
    to match how the live gateway validates responses.
    """
    system = item.system
    response_format = None
    if item.response_model is not None:
        schema = item.response_model.model_json_schema()
        system = system + _STRUCTURED_OUTPUT_INSTRUCTIONS.format(schema=json.dumps(schema))
        response_format = {"type": "json_object"}

    body: dict = {
        "model": item.model,
        "messages": [*_system_messages(system), {"role": "user", "content": item.user}],
        "max_completion_tokens": _completion_tokens_for(item.model, item.max_tokens),
    }
    if response_format is not None:
        body["response_format"] = response_format
    if item.temperature is not None and _supports_custom_temperature(item.model):
        body["temperature"] = item.temperature
    return body


class OpenAIBatchService:
    """Submit, poll, and read back an OpenAI Batch."""

    def __init__(self, api_key: str | None = None):
        self._client = AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
            max_retries=0,
            timeout=settings.llm_request_timeout_seconds,
        )

    async def submit(self, items: list[BatchRequestItem]) -> str:
        """Upload the JSONL request file, create the batch, return its id."""
        lines = [
            json.dumps({
                "custom_id": item.custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": _build_body(item),
            })
            for item in items
        ]
        jsonl_bytes = ("\n".join(lines) + "\n").encode("utf-8")
        uploaded = await self._client.files.create(
            file=("batch_input.jsonl", jsonl_bytes, "application/jsonl"),
            purpose="batch",
        )
        batch = await self._client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        return batch.id

    async def status(self, batch_id: str) -> BatchStatus:
        batch = await self._client.batches.retrieve(batch_id)
        counts = batch.request_counts
        return BatchStatus(
            batch_id=batch.id,
            processing_status=batch.status,
            request_counts={
                "total": counts.total,
                "completed": counts.completed,
                "failed": counts.failed,
            },
            ended=batch.status in _ENDED_STATUSES,
        )

    async def cancel(self, batch_id: str) -> str:
        """Request cancellation. In-flight requests may still complete."""
        batch = await self._client.batches.cancel(batch_id)
        return batch.status

    async def results(
        self,
        batch_id: str,
        *,
        response_models: dict[str, Type[BaseModel]] | None = None,
    ) -> list[BatchResultItem]:
        """Read back results for an ended batch.

        `response_models` maps custom_id -> the Pydantic model that
        request's item.response_model was set to, mirroring
        AnthropicBatchService.results(). Reads both the output file
        (succeeded + per-row API errors) and the error file (rows OpenAI
        never attempted), since a batch can have both -- see request_counts
        on status() to know what to expect.
        """
        response_models = response_models or {}
        batch = await self._client.batches.retrieve(batch_id)
        out: dict[str, BatchResultItem] = {}

        if batch.error_file_id:
            content = await self._client.files.content(batch.error_file_id)
            for row in _parse_jsonl(content.text):
                out[row["custom_id"]] = BatchResultItem(
                    custom_id=row["custom_id"],
                    status="errored",
                    error=json.dumps(row.get("error")),
                )

        if batch.output_file_id:
            content = await self._client.files.content(batch.output_file_id)
            for row in _parse_jsonl(content.text):
                out[row["custom_id"]] = _result_from_output_row(row, response_models)

        return list(out.values())


def _parse_jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _result_from_output_row(
    row: dict,
    response_models: dict[str, Type[BaseModel]],
) -> BatchResultItem:
    custom_id = row["custom_id"]
    response = row.get("response")
    if response is None or response.get("status_code") != 200:
        return BatchResultItem(
            custom_id=custom_id,
            status="errored",
            error=json.dumps(row.get("error") or response),
        )

    body = response["body"]
    message = body["choices"][0]["message"]
    text = message.get("content")
    usage = body.get("usage") or {}
    cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0

    parsed = None
    response_model = response_models.get(custom_id)
    if response_model is not None and text:
        try:
            parsed = response_model.model_validate_json(text)
        except ValidationError:
            parsed = None  # caller can inspect .text/.error and retry

    return BatchResultItem(
        custom_id=custom_id,
        status="succeeded",
        text=text,
        parsed=parsed,
        input_tokens=usage.get("prompt_tokens", 0) or 0,
        output_tokens=usage.get("completion_tokens", 0) or 0,
        cache_read_input_tokens=cached_tokens,
        model=body.get("model"),
    )
