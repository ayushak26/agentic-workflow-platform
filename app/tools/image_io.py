"""OpenAI image-generation service used by workflow nodes."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from openai import AsyncOpenAI

from app.config import Settings, settings
from app.llm.openai_registry import (
    OPENAI_IMAGE_FALLBACK_CHAINS,
    OPENAI_IMAGE_MODEL_NAMES,
    require_openai_model_kind,
)


OpenAIImageModel: TypeAlias = Literal[
    "gpt-image-2-2026-04-21",
    "chatgpt-image-latest",
    "dall-e-2",
]
OPENAI_IMAGE_MODELS: tuple[OpenAIImageModel, ...] = OPENAI_IMAGE_MODEL_NAMES


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    model: str
    output_format: str
    requested_model: str
    fallback: bool = False
    revised_prompt: str | None = None


class OpenAIImageGenerationService:
    def __init__(
        self,
        app_settings: Settings = settings,
        *,
        client: Any | None = None,
    ) -> None:
        self.settings = app_settings
        self._client = client

    def available(self) -> bool:
        return bool(
            self.settings.image_generation_backend == "openai"
            and self.settings.openai_api_key.strip()
        )

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "auto",
        quality: str = "auto",
        output_format: str = "png",
        model: OpenAIImageModel | None = None,
    ) -> GeneratedImage:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("image prompt cannot be empty")
        if self.settings.image_generation_backend != "openai":
            raise RuntimeError(
                "OpenAI image generation is disabled by "
                "IMAGE_GENERATION_BACKEND"
            )
        key = self.settings.openai_api_key.strip()
        if self._client is None and not key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        client = self._client or AsyncOpenAI(
            api_key=key,
            max_retries=0,
            timeout=self.settings.llm_request_timeout_seconds,
        )
        selected_model = model or self.settings.openai_image_model
        require_openai_model_kind(selected_model, "image")
        candidates = (
            selected_model,
            *OPENAI_IMAGE_FALLBACK_CHAINS.get(selected_model, ()),
        )
        last_error: BaseException | None = None
        for candidate in candidates:
            try:
                response = await client.images.generate(
                    **_image_request_kwargs(
                        model=candidate,
                        prompt=prompt,
                        size=size,
                        quality=quality,
                        output_format=output_format,
                    )
                )
                item = response.data[0]
                encoded = getattr(item, "b64_json", None)
                if not encoded:
                    raise RuntimeError(
                        "OpenAI image response did not include bytes"
                    )
                return GeneratedImage(
                    data=base64.b64decode(encoded, validate=True),
                    model=candidate,
                    requested_model=selected_model,
                    fallback=candidate != selected_model,
                    output_format=output_format,
                    revised_prompt=getattr(item, "revised_prompt", None),
                )
            except Exception as exc:
                last_error = exc
                if not _model_unavailable(exc):
                    raise
        assert last_error is not None
        raise last_error


def _image_request_kwargs(
    *,
    model: str,
    prompt: str,
    size: str,
    quality: str,
    output_format: str,
) -> dict[str, Any]:
    require_openai_model_kind(model, "image")
    if model == "dall-e-2":
        if output_format != "png":
            raise ValueError("dall-e-2 supports PNG output only")
        resolved_size = "1024x1024" if size == "auto" else size
        if resolved_size not in {"256x256", "512x512", "1024x1024"}:
            raise ValueError(
                "dall-e-2 size must be 256x256, 512x512, or 1024x1024"
            )
        return {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": resolved_size,
            "response_format": "b64_json",
        }
    return {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "output_format": output_format,
    }


def _model_unavailable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if not isinstance(status, int):
        status = getattr(response, "status_code", None)
    if status in {403, 404}:
        return True
    body = getattr(exc, "body", None)
    code = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
    return code.lower() == "model_not_found" or (
        "model_not_found" in str(exc).lower()
    )


_default_service: OpenAIImageGenerationService | None = None


def get_image_generation_service() -> OpenAIImageGenerationService:
    global _default_service
    if _default_service is None:
        _default_service = OpenAIImageGenerationService()
    return _default_service
