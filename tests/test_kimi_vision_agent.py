from __future__ import annotations

from dataclasses import dataclass

import app.nodes  # noqa: F401
import pytest

from app.nodes.kimi_vision_agent import KimiVisionAgent
from app.runtime.schema import WorkflowFileRef


def _ref(**overrides) -> WorkflowFileRef:
    defaults = dict(
        file_id="wf_abc123",
        name="diagram.png",
        extension=".png",
        category="image",
        content_type="image/png",
        size_bytes=3,
        sha256="a" * 64,
        minio_key="workflow-inputs/scope/abc.png",
        parseable_text=False,
    )
    defaults.update(overrides)
    return WorkflowFileRef(**defaults)


class FakeObjectStore:
    def __init__(self, blobs: dict[str, bytes]):
        self.blobs = blobs

    def get_bytes(self, key: str) -> bytes:
        return self.blobs[key]


@dataclass
class _FakeAnalysis:
    text: str
    model: str
    input_tokens: int = 10
    output_tokens: int = 20


class StubVisionService:
    def __init__(self, text: str = "a diagram"):
        self.text = text
        self.calls: list[dict] = []

    async def analyze(self, image_bytes, *, content_type, prompt, model, max_completion_tokens):
        self.calls.append({
            "content_type": content_type,
            "prompt": prompt,
            "model": model,
        })
        return _FakeAnalysis(text=self.text, model=model)


def _node(services):
    return KimiVisionAgent("understand_image", {"image": None}, services=services)


@pytest.mark.asyncio
async def test_absent_optional_image_is_a_clean_no_op_not_an_error():
    node = _node({})
    result = await node.run(state={}, resolved_config={"image": None})
    assert result["skipped"] is True
    assert result["analysis"] == ""
    assert result["minio_key"] == ""


@pytest.mark.asyncio
async def test_analyzes_a_resolved_image_reference():
    ref = _ref()
    store = FakeObjectStore({ref.minio_key: b"png-bytes"})
    service = StubVisionService(text="It shows a work-package diagram.")
    node = _node({"kimi_vision": service, "object_store": store})

    result = await node.run(
        state={},
        resolved_config={"image": ref.model_dump(), "prompt": "Describe it."},
    )

    assert result["skipped"] is False
    assert result["analysis"] == "It shows a work-package diagram."
    assert result["minio_key"] == ref.minio_key
    assert result["byte_size"] == len(b"png-bytes")
    assert service.calls[0]["prompt"] == "Describe it."


@pytest.mark.asyncio
async def test_non_image_category_is_rejected():
    ref = _ref(category="document", extension=".pdf", content_type="application/pdf")
    node = _node({"kimi_vision": StubVisionService(), "object_store": FakeObjectStore({})})

    with pytest.raises(ValueError, match="image files only"):
        await node.run(state={}, resolved_config={"image": ref.model_dump()})


@pytest.mark.asyncio
async def test_unresolved_template_string_is_rejected():
    node = _node({"kimi_vision": StubVisionService(), "object_store": FakeObjectStore({})})

    with pytest.raises(ValueError, match="did not resolve"):
        await node.run(state={}, resolved_config={"image": "{{inputs.image}}"})
