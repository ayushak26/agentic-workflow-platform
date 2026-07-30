"""OpenAI image-generation service used by workflow nodes."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from openai import AsyncOpenAI

from app.config import Settings, settings


OpenAIImageModel: TypeAlias = Literal[
    "gpt-image-2-2026-04-21",
    "chatgpt-image-latest",
]
OPENAI_IMAGE_MODELS: tuple[OpenAIImageModel, ...] = (
    "gpt-image-2-2026-04-21",
    "chatgpt-image-latest",
)


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    model: str
    output_format: str
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
        if selected_model not in OPENAI_IMAGE_MODELS:
            raise ValueError(
                f"OpenAI image model {selected_model!r} is not approved"
            )
        response = await client.images.generate(
            model=selected_model,
            prompt=prompt,
            n=1,
            size=size,
            quality=quality,
            output_format=output_format,
        )
        item = response.data[0]
        encoded = getattr(item, "b64_json", None)
        if not encoded:
            raise RuntimeError("OpenAI image response did not include bytes")
        return GeneratedImage(
            data=base64.b64decode(encoded, validate=True),
            model=selected_model,
            output_format=output_format,
            revised_prompt=getattr(item, "revised_prompt", None),
        )


_default_service: OpenAIImageGenerationService | None = None


def get_image_generation_service() -> OpenAIImageGenerationService:
    global _default_service
    if _default_service is None:
        _default_service = OpenAIImageGenerationService()
    return _default_service
