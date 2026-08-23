from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.nodes.openai_image_generation_agent import OpenAIImageGenerationAgent
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import preflight_workflow_yaml
from app.tools.image_io import OpenAIImageGenerationService


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeOpenRouterClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def post(self, path, *, json):
        self.calls.append((path, json))
        return FakeResponse(self.payload)


class FakeStore:
    def __init__(self):
        self.puts = []

    def put_bytes(self, data, key, content_type=None):
        self.puts.append((data, key, content_type))


@pytest.mark.asyncio
async def test_openrouter_image_api_maps_to_shared_generated_image_contract():
    image_bytes = b"real-image-bytes"
    client = FakeOpenRouterClient({
        "model": "google/gemini-3.1-flash-image",
        "data": [{
            "b64_json": base64.b64encode(image_bytes).decode(),
            "media_type": "image/png",
        }],
    })
    service = OpenAIImageGenerationService(
        Settings(_env_file=None, openrouter_api_key="test-key"),
        openrouter_client=client,
    )

    result = await service.generate(
        "A STAR interview memory aid",
        provider="openrouter",
        model="google/gemini-3.1-flash-image",
        size="1024x1024",
        quality="medium",
        output_format="png",
    )

    assert result.data == image_bytes
    assert result.model == "google/gemini-3.1-flash-image"
    assert result.output_format == "png"
    assert client.calls == [("images", {
        "model": "google/gemini-3.1-flash-image",
        "prompt": "A STAR interview memory aid",
        "n": 1,
        "size": "1024x1024",
        "quality": "medium",
        "output_format": "png",
    })]


@pytest.mark.asyncio
async def test_image_node_persists_openrouter_result_with_real_provider_metadata():
    image = SimpleNamespace(
        data=b"png", model="openrouter-image-model", output_format="png",
        revised_prompt=None,
    )

    class Service:
        async def generate(self, prompt, **kwargs):
            assert prompt == "visual prompt"
            assert kwargs["provider"] == "openrouter"
            assert kwargs["model"] == "google/gemini-3.1-flash-image"
            return image

    store = FakeStore()
    node = OpenAIImageGenerationAgent("visual", {
        "prompt": "visual prompt",
        "backend": "openrouter",
        "openrouter_image_model": "google/gemini-3.1-flash-image",
    }, services={"image_generator": Service(), "object_store": store})

    output = await node.run({"inputs": {"SYSTEM.run_id": "run-1"}}, node.config.model_dump())

    assert output["provider"] == "openrouter"
    assert output["model"] == "openrouter-image-model"
    assert output["minio_key"] == "workflows/run-1/images/visual.png"
    assert store.puts == [(b"png", "workflows/run-1/images/visual.png", "image/png")]


def test_provider_specific_availability_uses_the_matching_credential():
    service = OpenAIImageGenerationService(Settings(
        _env_file=None,
        openai_api_key="",
        openrouter_api_key="or-key",
    ))
    assert service.available("openai") is False
    assert service.available("openrouter") is True


def test_multimodal_interview_prep_workflow_is_publishable_and_preflight_clean():
    text = Path("workflows/interview_prep_multimodal.yaml").read_text(encoding="utf-8")
    spec = load_workflow_from_string(text)
    report = preflight_workflow_yaml(text)

    assert spec.library is not None
    assert spec.library.visibility_status == "approved"
    assert {node.type for node in spec.nodes} == {
        "StartAgent", "TransformAgent", "OpenAIImageGenerationAgent", "EndAgent",
    }
    image_nodes = [node for node in spec.nodes if node.type == "OpenAIImageGenerationAgent"]
    assert {node.config["backend"] for node in image_nodes} == {"openai", "openrouter"}
    coach = next(node for node in spec.nodes if node.id == "coach")
    assert str(coach.selected_model).startswith("openrouter/")
    assert report.valid, [issue.message for issue in report.errors]