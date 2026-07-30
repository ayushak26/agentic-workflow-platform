"""Generate one OpenAI image and persist it in object storage."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.tools.image_io import OpenAIImageModel


class ImageGenerationInput(BaseModel):
    pass


class ImageGenerationConfig(BaseModel):
    prompt: str
    backend: Literal["disabled", "openai"] = Field(
        default="openai",
        description="Disable image creation or use OpenAI image generation.",
        json_schema_extra={
            "x-enum-labels": {
                "disabled": "Disabled",
                "openai": "OpenAI image generation",
            }
        },
    )
    image_model: OpenAIImageModel = Field(
        default="gpt-image-2-2026-04-21",
        description="Approved OpenAI image model for this node.",
        json_schema_extra={
            "x-enum-labels": {
                "gpt-image-2-2026-04-21": (
                    "GPT Image 2 — pinned 2026-04-21 (recommended)"
                ),
                "chatgpt-image-latest": (
                    "ChatGPT image latest — compatibility"
                ),
            }
        },
    )
    size: str = "auto"
    quality: Literal["auto", "low", "medium", "high"] = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"


class ImageGenerationOutput(BaseModel):
    generated: bool
    provider: str
    model: str | None = None
    minio_key: str | None = None
    content_type: str | None = None
    byte_size: int = 0
    revised_prompt: str | None = None


@NodeRegistry.register
class OpenAIImageGenerationAgent(NodeType):
    type_name = "OpenAIImageGenerationAgent"
    description = (
        "Generate an image with an approved OpenAI image model and store the "
        "bytes in object storage."
    )
    input_schema = ImageGenerationInput
    config_schema = ImageGenerationConfig
    output_schema = ImageGenerationOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = ImageGenerationConfig(**resolved_config)
        if cfg.backend == "disabled":
            return {"generated": False, "provider": "disabled"}
        service = self.services.get("image_generator")
        store = self.services.get("object_store")
        if service is None or store is None:
            raise RuntimeError(
                "OpenAIImageGenerationAgent requires image_generator and "
                "object_store services"
            )
        image = await service.generate(
            cfg.prompt,
            size=cfg.size,
            quality=cfg.quality,
            output_format=cfg.output_format,
            model=cfg.image_model,
        )
        run_id = state.get("inputs", {}).get(
            "SYSTEM.run_id",
            str(uuid.uuid4()),
        )
        extension = "jpg" if cfg.output_format == "jpeg" else cfg.output_format
        content_type = f"image/{cfg.output_format}"
        key = (
            f"workflows/{run_id}/images/{self.node_id}.{extension}"
        )
        await asyncio.to_thread(
            store.put_bytes,
            image.data,
            key,
            content_type=content_type,
        )
        return {
            "generated": True,
            "provider": "openai",
            "model": image.model,
            "minio_key": key,
            "content_type": content_type,
            "byte_size": len(image.data),
            "revised_prompt": image.revised_prompt,
        }
