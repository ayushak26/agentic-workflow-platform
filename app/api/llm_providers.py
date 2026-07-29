"""Safe model-catalog and local-provider status endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.llm.catalog import MODEL_CATALOG, is_local_model
from app.llm.model_catalog import AUTO_MODEL, AUTO_MODEL_LABEL
from app.llm.registry import local_model_enabled, probe_local_model
from app.security.dependencies import (
    CurrentUser,
    require_admin,
    require_consultant,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])


def _configured(model: str, provider: str) -> bool:
    if model == "local-kimi-k3":
        return settings.local_kimi_enabled
    if model == "local-glm-5":
        return settings.local_glm_enabled
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    if provider == "openai":
        return bool(settings.openai_api_key)
    return False


@router.get("/models")
async def list_models(
    user: CurrentUser = Depends(require_consultant),
):
    """Return public capability metadata without URLs or credentials."""

    del user
    models = []
    for definition in MODEL_CATALOG:
        item = definition.as_dict()
        item["enabled"] = (
            local_model_enabled(definition.name)
            if definition.local
            else True
        )
        item["configured"] = _configured(
            definition.name,
            definition.provider,
        )
        models.append(item)
    auto_configured = any(
        item["enabled"] and item["configured"]
        for item in models
    )
    auto = {
        "name": AUTO_MODEL,
        "display_name": AUTO_MODEL_LABEL,
        "provider": "task-aware-router",
        "local": False,
        "automatic": True,
        "enabled": True,
        "configured": auto_configured,
        "tool_calling": True,
        "structured_output": True,
        "reasoning_efforts": [],
        "platform_modalities": ["text"],
        "upstream_url": None,
        "description": (
            "Deterministically chooses the best configured model for each "
            "call using task type, complexity, required capabilities, "
            "quality policy, cost, latency, and provider availability."
        ),
    }
    return {"models": [auto, *models]}


@router.post("/models/{model}/probe")
async def probe_model(
    model: str,
    user: CurrentUser = Depends(require_admin),
):
    """Probe an enabled local endpoint; never return its private address."""

    del user
    if not is_local_model(model):
        raise HTTPException(status_code=400, detail="model is not local")
    if not local_model_enabled(model):
        raise HTTPException(status_code=409, detail="local model is disabled")
    try:
        details = await asyncio.wait_for(
            probe_local_model(model),
            timeout=settings.health_probe_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="local model probe timed out") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"local model unavailable: {type(exc).__name__}",
        ) from exc
    return {"status": "ok", **details}
