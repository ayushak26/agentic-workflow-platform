"""Provider-neutral image generation used by workflow nodes.

OpenAI uses the Images SDK. OpenRouter uses its model-agnostic Image API. Both
paths return the same bounded ``GeneratedImage`` value so workflow nodes and
object storage do not depend on a provider-specific response shape.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

import httpx
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
ImageProvider: TypeAlias = Literal["openai", "openrouter"]
DEFAULT_OPENROUTER_IMAGE_MODEL = "google/gemini-3.1-flash-image"
_OPENROUTER_MEDIA_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}
_MAX_IMAGE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class GeneratedImage:
    """Provides the GeneratedImage behaviour.

    Attributes:
        data (bytes).
        model (str).
        output_format (str).
        requested_model (str).
        fallback (bool).
        revised_prompt (str | None).
    """
    data: bytes
    model: str
    output_format: str
    requested_model: str
    fallback: bool = False
    revised_prompt: str | None = None


class OpenAIImageGenerationService:
    """Provides the OpenAIImageGenerationService behaviour."""
    def __init__(
        self,
        app_settings: Settings = settings,
        *,
        client: Any | None = None,
        openrouter_client: Any | None = None,
    ) -> None:
        """Initialize the OpenAIImageGenerationService.

        Args:
            app_settings (Settings): The app settings (optional, default settings).
            client (Any | None): Client instance (optional, default None).
        """
        self.settings = app_settings
        self._client = client
        self._openrouter_client = openrouter_client

    def available(self, provider: str = "openai") -> bool:
        """Compute the available.

        Returns:
            bool: The result.
        """
        if provider == "openrouter":
            return bool(self.settings.openrouter_api_key.strip())
        return bool(self.settings.image_generation_backend == "openai" and self.settings.openai_api_key.strip())

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "auto",
        quality: str = "auto",
        output_format: str = "png",
        model: str | None = None,
        provider: ImageProvider = "openai",
    ) -> GeneratedImage:
        """Generate the result.

        Args:
            prompt (str): Prompt text.
            size (str): The size (optional, default 'auto').
            quality (str): The quality (optional, default 'auto').
            output_format (str): The output format (optional, default 'png').
            model (OpenAIImageModel | None): Model name (optional, default None).

        Returns:
            GeneratedImage: The result.
        """
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("image prompt cannot be empty")
        if provider == "openrouter":
            return await self._generate_openrouter(
                prompt,
                size=size,
                quality=quality,
                output_format=output_format,
                model=model or DEFAULT_OPENROUTER_IMAGE_MODEL,
            )
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

    async def _generate_openrouter(
        self,
        prompt: str,
        *,
        size: str,
        quality: str,
        output_format: str,
        model: str,
    ) -> GeneratedImage:
        key = self.settings.openrouter_api_key.strip()
        if self._openrouter_client is None and not key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        if size != "auto":
            body["size"] = size
        if quality != "auto":
            body["quality"] = quality
        if output_format != "auto":
            body["output_format"] = output_format

        owns_client = self._openrouter_client is None
        client = self._openrouter_client or httpx.AsyncClient(
            base_url=self.settings.openrouter_base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=max(self.settings.llm_request_timeout_seconds, 300.0),
        )
        try:
            response = await client.post("images", json=body)
            response.raise_for_status()
            payload = response.json()
        finally:
            if owns_client:
                await client.aclose()

        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise RuntimeError("OpenRouter image response contained no images")
        item = items[0]
        encoded = item.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError("OpenRouter image response did not include bytes")
        if encoded.startswith("data:") and "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("OpenRouter image response contained invalid base64") from exc
        if not data or len(data) > _MAX_IMAGE_BYTES:
            raise RuntimeError("OpenRouter image response exceeded the 25 MB safety limit")
        media_type = str(item.get("media_type") or f"image/{output_format}").lower()
        returned_format = _OPENROUTER_MEDIA_TYPES.get(media_type)
        if returned_format is None:
            raise RuntimeError(f"OpenRouter returned unsupported image type {media_type!r}")
        return GeneratedImage(
            data=data,
            model=str(payload.get("model") or model),
            requested_model=model,
            output_format=returned_format,
            revised_prompt=item.get("revised_prompt") if isinstance(item.get("revised_prompt"), str) else None,
        )


def _image_request_kwargs(
    *,
    model: str,
    prompt: str,
    size: str,
    quality: str,
    output_format: str,
) -> dict[str, Any]:
    """Internal helper for the image request kwargs step.

    Args:
        model (str): Model name.
        prompt (str): Prompt text.
        size (str): The size.
        quality (str): The quality.
        output_format (str): The output format.

    Returns:
        dict[str, Any]: The request kwargs.
    """
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
    """Internal helper for the model unavailable step.

    Args:
        exc (BaseException): Exception that was raised.

    Returns:
        bool: The unavailable.
    """
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
    """Return the image generation service.

    Returns:
        OpenAIImageGenerationService: The image generation service.
    """
    global _default_service
    if _default_service is None:
        _default_service = OpenAIImageGenerationService()
    return _default_service
