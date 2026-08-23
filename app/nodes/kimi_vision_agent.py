"""Understand one uploaded image with Kimi K3 vision."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.schema import WorkflowFileRef


class KimiVisionInput(BaseModel):
    """Pydantic model defining the KimiVisionInput shape."""
    pass


class KimiVisionConfig(BaseModel):
    # A string permits "{{inputs.image}}" during compile-time validation. The
    # runtime must resolve it to the complete scoped WorkflowFileRef. None
    # means an optional image input was declared but never supplied for this
    # run — a legitimate case (see run()), not a template-resolution bug.
    """Pydantic model defining the KimiVisionConfig shape.

    Attributes:
        image (str | WorkflowFileRef | None).
        prompt (str).
        vision_model (Literal['kimi-k3']).
        max_completion_tokens (int).
    """
    image: str | WorkflowFileRef | None
    prompt: str = "Describe and analyse this image."
    vision_model: Literal["kimi-k3"] = Field(
        default="kimi-k3",
        description="Kimi vision model.",
        json_schema_extra={
            "x-enum-labels": {"kimi-k3": "Kimi K3 vision"}
        },
    )
    max_completion_tokens: int = Field(default=8192, ge=1, le=32768)


class KimiVisionOutput(BaseModel):
    """Pydantic model defining the KimiVisionOutput shape.

    Attributes:
        analysis (str).
        provider (Literal['kimi']).
        model (str).
        image_name (str).
        minio_key (str).
        content_type (str).
        byte_size (int).
        input_tokens (int).
    """
    analysis: str
    provider: Literal["kimi"] = "kimi"
    model: str
    image_name: str
    minio_key: str
    content_type: str
    byte_size: int
    input_tokens: int = 0
    output_tokens: int = 0
    skipped: bool = False


@NodeRegistry.register
class KimiVisionAgent(NodeType):
    """Workflow node type implementing the KimiVisionAgent capability."""
    type_name = "KimiVisionAgent"
    description = (
        "Analyse an uploaded image with Kimi K3. Image bytes are fetched from "
        "object storage and are never written to workflow state."
    )
    input_schema = KimiVisionInput
    config_schema = KimiVisionConfig
    output_schema = KimiVisionOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"object_store", "kimi_vision"}

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the result.

        Args:
            state (dict[str, Any]): Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        _ = state
        cfg = KimiVisionConfig.model_validate(resolved_config)
        if cfg.image is None:
            # An optional image input this run simply didn't supply — not a
            # misconfiguration, so this returns a clean no-op rather than
            # raising.
            return {
                "analysis": "",
                "provider": "kimi",
                "model": cfg.vision_model,
                "image_name": "",
                "minio_key": "",
                "content_type": "",
                "byte_size": 0,
                "skipped": True,
            }
        if isinstance(cfg.image, str):
            raise ValueError(
                "KimiVisionAgent.image did not resolve to an uploaded "
                "workflow-file reference"
            )
        if cfg.image.category != "image":
            raise ValueError("KimiVisionAgent accepts image files only")

        service = self.services.get("kimi_vision")
        store = self.services.get("object_store")
        if service is None or store is None:
            raise RuntimeError(
                "KimiVisionAgent requires kimi_vision and object_store services"
            )
        raw = await asyncio.to_thread(store.get_bytes, cfg.image.minio_key)
        result = await service.analyze(
            raw,
            content_type=cfg.image.content_type,
            prompt=cfg.prompt,
            model=cfg.vision_model,
            max_completion_tokens=cfg.max_completion_tokens,
        )
        return {
            "analysis": result.text,
            "provider": "kimi",
            "model": result.model,
            "image_name": cfg.image.name,
            "minio_key": cfg.image.minio_key,
            "content_type": cfg.image.content_type,
            "byte_size": len(raw),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "skipped": False,
        }
