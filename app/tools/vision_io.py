"""Kimi K3 vision service for image understanding."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from openai import AsyncOpenAI

from app.config import Settings, settings


KimiVisionModel: TypeAlias = Literal["kimi-k3"]
KIMI_VISION_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/heic",
        "image/heif",
    }
)


@dataclass(frozen=True)
class VisionAnalysis:
    """Provides the VisionAnalysis behaviour.

    Attributes:
        text (str).
        model (str).
        input_tokens (int).
        output_tokens (int).
    """
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class KimiVisionService:
    """Send an object-storage image to Kimi as a base64 content part."""

    def __init__(
        self,
        app_settings: Settings = settings,
        *,
        client: Any | None = None,
    ) -> None:
        """Initialize the KimiVisionService.

        Args:
            app_settings (Settings): The app settings (optional, default settings).
            client (Any | None): Client instance (optional, default None).
        """
        self.settings = app_settings
        self._client = client

    def available(self) -> bool:
        """Compute the available.

        Returns:
            bool: The result.
        """
        return bool(self.settings.moonshot_api_key.strip())

    async def analyze(
        self,
        image_bytes: bytes,
        *,
        content_type: str,
        prompt: str,
        model: KimiVisionModel = "kimi-k3",
        max_completion_tokens: int = 8192,
    ) -> VisionAnalysis:
        """Compute the analyze.

        Args:
            image_bytes (bytes): The image bytes.
            content_type (str): The content type.
            prompt (str): Prompt text.
            model (KimiVisionModel): Model name (optional, default 'kimi-k3').
            max_completion_tokens (int): The max completion tokens (optional, default 8192).

        Returns:
            VisionAnalysis: The result.
        """
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("vision prompt cannot be empty")
        if not image_bytes:
            raise ValueError("image cannot be empty")
        if len(image_bytes) > self.settings.kimi_vision_max_image_bytes:
            raise ValueError(
                "image exceeds KIMI_VISION_MAX_IMAGE_BYTES "
                f"({self.settings.kimi_vision_max_image_bytes} bytes)"
            )
        normalized_type = content_type.lower().strip()
        if normalized_type not in KIMI_VISION_CONTENT_TYPES:
            raise ValueError(
                f"Kimi vision does not support content type {content_type!r}"
            )
        if model != self.settings.kimi_vision_model:
            raise ValueError(f"Kimi vision model {model!r} is not approved")

        key = self.settings.moonshot_api_key.strip()
        if self._client is None and not key:
            raise RuntimeError("LOCAL_KIMI_API_KEY is not configured")
        client = self._client or AsyncOpenAI(
            api_key=key,
            base_url=self.settings.kimi_api_base_url,
            max_retries=0,
            timeout=self.settings.llm_request_timeout_seconds,
        )
        image_url = (
            f"data:{normalized_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Kimi. Analyse only the supplied visual and "
                        "clearly distinguish observations from inferences."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            max_completion_tokens=max_completion_tokens,
        )
        message = completion.choices[0].message
        usage = getattr(completion, "usage", None)
        return VisionAnalysis(
            text=str(message.content or ""),
            model=model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(
                getattr(usage, "completion_tokens", 0) or 0
            ),
        )


_default_service: KimiVisionService | None = None


def get_kimi_vision_service() -> KimiVisionService:
    """Return the kimi vision service.

    Returns:
        KimiVisionService: The kimi vision service.
    """
    global _default_service
    if _default_service is None:
        _default_service = KimiVisionService()
    return _default_service
