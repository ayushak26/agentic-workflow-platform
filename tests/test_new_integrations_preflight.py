from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.runtime.preflight import preflight_workflow_for_run
from app.tools.image_io import OpenAIImageGenerationService
from app.tools.vision_io import KimiVisionService
from app.tools.web_io import WebSearchService


class _WorkingObjectStoreClient:
    def list_buckets(self):
        return {"Buckets": []}


def _working_object_store():
    return SimpleNamespace(client=_WorkingObjectStoreClient())

WEB_SEARCH_WORKFLOW = """
name: web search test
nodes:
  - id: search
    type: WebSearchAgent
    config:
      query: bioeconomy funding
      provider: auto
"""

IMAGE_GENERATION_WORKFLOW = """
name: image generation test
nodes:
  - id: illustrate
    type: OpenAIImageGenerationAgent
    config:
      prompt: a diagram of a circular economy
"""

IMAGE_GENERATION_DISABLED_WORKFLOW = """
name: image generation disabled test
nodes:
  - id: illustrate
    type: OpenAIImageGenerationAgent
    config:
      prompt: a diagram of a circular economy
      backend: disabled
"""

KIMI_VISION_WORKFLOW = """
name: kimi vision test
inputs:
  image:
    type: file
nodes:
  - id: understand
    type: KimiVisionAgent
    config:
      image: "{{inputs.image}}"
"""


@pytest.mark.asyncio
async def test_web_search_agent_blocked_without_any_provider_configured():
    settings = Settings(_env_file=None)
    report = await preflight_workflow_for_run(
        WEB_SEARCH_WORKFLOW,
        provided_inputs={},
        services={"web_search": WebSearchService(settings)},
        require_run_history=False,
    )
    assert report.valid is False
    assert any(
        issue.code == "WEB_SEARCH_PROVIDER_UNAVAILABLE"
        for issue in report.errors
    )


@pytest.mark.asyncio
async def test_web_search_agent_passes_once_a_provider_is_configured():
    settings = Settings(_env_file=None, tavily_api_key="tvly-real-key")
    report = await preflight_workflow_for_run(
        WEB_SEARCH_WORKFLOW,
        provided_inputs={},
        services={"web_search": WebSearchService(settings)},
        require_run_history=False,
    )
    assert report.valid, [i.message for i in report.errors]


@pytest.mark.asyncio
async def test_image_generation_blocked_without_openai_key():
    settings = Settings(_env_file=None, openai_api_key="")
    report = await preflight_workflow_for_run(
        IMAGE_GENERATION_WORKFLOW,
        provided_inputs={},
        services={
            "image_generator": OpenAIImageGenerationService(settings),
            "object_store": _working_object_store(),
        },
        require_run_history=False,
    )
    assert report.valid is False
    assert any(
        issue.code == "IMAGE_GENERATION_UNAVAILABLE" for issue in report.errors
    )


@pytest.mark.asyncio
async def test_image_generation_disabled_backend_needs_no_credentials():
    report = await preflight_workflow_for_run(
        IMAGE_GENERATION_DISABLED_WORKFLOW,
        provided_inputs={},
        services={},
        require_run_history=False,
    )
    assert report.valid, [i.message for i in report.errors]


@pytest.mark.asyncio
async def test_kimi_vision_blocked_without_moonshot_key():
    settings = Settings(_env_file=None, local_kimi_api_key="")
    report = await preflight_workflow_for_run(
        KIMI_VISION_WORKFLOW,
        provided_inputs={},
        services={
            "kimi_vision": KimiVisionService(settings),
            "object_store": _working_object_store(),
        },
        require_run_history=False,
    )
    assert report.valid is False
    assert any(issue.code == "KIMI_VISION_UNAVAILABLE" for issue in report.errors)


@pytest.mark.asyncio
async def test_kimi_vision_passes_with_moonshot_key_configured():
    settings = Settings(_env_file=None, local_kimi_api_key="sk-real-key")
    report = await preflight_workflow_for_run(
        KIMI_VISION_WORKFLOW,
        provided_inputs={},
        services={
            "kimi_vision": KimiVisionService(settings),
            "object_store": _working_object_store(),
        },
        require_run_history=False,
    )
    assert report.valid, [i.message for i in report.errors]
