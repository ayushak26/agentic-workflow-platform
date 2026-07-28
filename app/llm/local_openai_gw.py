"""OpenAI-compatible gateway for privately hosted Kimi K3 and GLM-5.

The inference engine is external to the API container. vLLM and SGLang both
expose the endpoints used here. Provider URLs and served model identifiers are
deployment settings; workflows can select only the stable catalog aliases.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Type, TypeVar
from urllib.parse import urlsplit, urlunsplit

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMGateway, LLMResponse, LLMToolUseResponse, ToolCall
from app.llm.errors import StructuredOutputError
from app.llm.openai_gw import StructuredResult

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LocalModelProfile:
    alias: str
    provider: str
    enabled: bool
    base_url: str
    api_key: str
    served_model: str
    reasoning_effort: str
    enable_thinking: bool = True
    timeout_seconds: float = 600.0
    verify_served_model: bool = True


def normalize_openai_base_url(value: str) -> str:
    """Validate and normalize a deployment-owned OpenAI-compatible URL."""

    raw = value.strip().rstrip("/")
    if not raw:
        raise ValueError("local LLM base URL cannot be empty")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("local LLM base URL must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("local LLM credentials must not be embedded in the URL")
    if parsed.query or parsed.fragment:
        raise ValueError("local LLM base URL cannot contain query or fragment data")
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _reasoning_content(message: Any) -> str | None:
    direct = getattr(message, "reasoning_content", None)
    if isinstance(direct, str):
        return direct
    extra = getattr(message, "model_extra", None) or {}
    value = extra.get("reasoning_content")
    return value if isinstance(value, str) else None


def _usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


class LocalOpenAICompatibleGateway(LLMGateway):
    """Gateway used by one configured local/private model endpoint."""

    def __init__(
        self,
        profile: LocalModelProfile,
        *,
        client: Any | None = None,
    ) -> None:
        if not profile.enabled:
            raise ValueError(f"{profile.alias} is disabled")
        if not profile.served_model.strip():
            raise ValueError(f"{profile.alias} served model cannot be empty")
        self.profile = profile
        self.base_url = normalize_openai_base_url(profile.base_url)
        self._client = client or AsyncOpenAI(
            api_key=profile.api_key.strip() or "local-no-auth",
            base_url=self.base_url,
            timeout=profile.timeout_seconds,
            max_retries=0,
        )

    def _generation_options(self) -> dict[str, Any]:
        # ``extra_body`` keeps compatibility with OpenAI SDK versions that do
        # not yet type provider-specific fields while still placing them at the
        # top level of the JSON request sent to vLLM/SGLang.
        extra_body: dict[str, Any] = {
            "reasoning_effort": self.profile.reasoning_effort,
        }
        if self.profile.provider == "zai-local":
            extra_body["enable_thinking"] = self.profile.enable_thinking
        return {"extra_body": extra_body}

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self.profile.served_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **self._generation_options(),
        )
        choice = response.choices[0]
        input_tokens, output_tokens = _usage(response)
        return LLMResponse(
            text=choice.message.content or "",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=choice.finish_reason,
        )

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> StructuredResult:
        schema = response_model.model_json_schema()
        response = await self._client.chat.completions.create(
            model=self.profile.served_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__.lower(),
                    "strict": True,
                    "schema": schema,
                },
            },
            **self._generation_options(),
        )
        content = response.choices[0].message.content or ""
        try:
            parsed = response_model.model_validate_json(content)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise StructuredOutputError(
                f"{self.profile.alias} returned output that violated the "
                f"constrained schema for {response_model.__name__}"
            ) from exc
        input_tokens, output_tokens = _usage(response)
        return StructuredResult(
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )

    async def chat_with_tools(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMToolUseResponse:
        openai_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ]
        for message in messages:
            role = message["role"]
            if role == "user":
                openai_messages.append(
                    {"role": "user", "content": message["content"]}
                )
            elif role == "assistant":
                assistant: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content"),
                }
                if message.get("reasoning_content"):
                    assistant["reasoning_content"] = message["reasoning_content"]
                if message.get("tool_calls"):
                    assistant["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                        for call in message["tool_calls"]
                    ]
                openai_messages.append(assistant)
            elif role == "tool":
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                )

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]
        kwargs: dict[str, Any] = {
            "model": self.profile.served_model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._generation_options(),
        }
        if openai_tools:
            kwargs["tools"] = openai_tools
            kwargs["tool_choice"] = "auto"
        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        tool_calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments)
            except (TypeError, json.JSONDecodeError) as exc:
                raise StructuredOutputError(
                    f"{self.profile.alias} returned invalid tool arguments "
                    f"for {call.function.name}"
                ) from exc
            tool_calls.append(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                )
            )
        input_tokens, output_tokens = _usage(response)
        return LLMToolUseResponse(
            text=message.content,
            reasoning_content=_reasoning_content(message),
            tool_calls=tool_calls,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=choice.finish_reason,
        )

    async def probe_details(self) -> dict[str, Any]:
        response = await self._client.models.list()
        model_ids = {
            item.id
            for item in getattr(response, "data", [])
            if isinstance(getattr(item, "id", None), str)
        }
        if (
            self.profile.verify_served_model
            and self.profile.served_model not in model_ids
        ):
            raise RuntimeError("configured served model was not listed")
        return {
            "provider": self.profile.provider,
            "model": self.profile.alias,
            "served_model_available": self.profile.served_model in model_ids,
            "model_count": len(model_ids),
        }

    async def probe(self) -> bool:
        await self.probe_details()
        return True


class KimiK3LocalGateway(LocalOpenAICompatibleGateway):
    """Distinct class keeps Kimi and GLM instances separate in the registry."""


class GLM5LocalGateway(LocalOpenAICompatibleGateway):
    """Distinct class keeps Kimi and GLM instances separate in the registry."""
