from __future__ import annotations

import base64

import app.nodes  # noqa: F401
import pytest

from app.nodes.figure_embedder import FigureEmbedder


class FakeObjectStore:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def get_bytes(self, key: str) -> bytes:
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key]


def _node(services):
    # __init__ eagerly validates raw_config; run() re-validates its own
    # resolved_config independently (matching other nodes' pattern), so the
    # constructor just needs any minimally valid placeholder here.
    return FigureEmbedder("figure_embedder", {"content": ""}, services=services)


@pytest.mark.asyncio
async def test_matched_marker_is_replaced_with_data_uri():
    image_bytes = b"\x89PNG fake bytes"
    store = FakeObjectStore({"figures/oro.png": image_bytes})
    node = _node({"object_store": store})

    result = await node.run(
        state={},
        resolved_config={
            "content": (
                "Intro.\n\n[[IMAGE PROMPT: Figure 1 - ORO Chain]]\n\nOutro."
            ),
            "figures": [
                {
                    "marker": "Figure 1 - ORO Chain",
                    "image": {
                        "minio_key": "figures/oro.png",
                        "content_type": "image/png",
                    },
                    "alt_text": "ORO chain diagram",
                }
            ],
        },
    )

    assert result["embedded_count"] == 1
    assert result["unmatched_figures"] == []
    assert result["missing_images"] == []
    expected_data_uri = (
        f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
    )
    assert f"![ORO chain diagram]({expected_data_uri})" in result["content"]
    assert "[[IMAGE PROMPT:" not in result["content"]


@pytest.mark.asyncio
async def test_marker_match_is_case_insensitive_and_whitespace_tolerant():
    store = FakeObjectStore({"figures/x.png": b"bytes"})
    node = _node({"object_store": store})

    result = await node.run(
        state={},
        resolved_config={
            "content": "[[image prompt:   My Figure   ]]",
            "figures": [
                {"marker": "My Figure", "image": {"minio_key": "figures/x.png"}}
            ],
        },
    )
    assert result["embedded_count"] == 1


@pytest.mark.asyncio
async def test_unmatched_marker_is_reported_and_content_left_untouched():
    node = _node({"object_store": FakeObjectStore({})})

    result = await node.run(
        state={},
        resolved_config={
            "content": "No figure markers in here at all.",
            "figures": [
                {"marker": "Never appears", "image": {"minio_key": "figures/x.png"}}
            ],
        },
    )
    assert result["embedded_count"] == 0
    assert result["unmatched_figures"] == ["Never appears"]
    assert result["content"] == "No figure markers in here at all."


@pytest.mark.asyncio
async def test_missing_image_leaves_marker_in_place_for_placeholder_fallback():
    node = _node({"object_store": FakeObjectStore({})})  # key not present

    result = await node.run(
        state={},
        resolved_config={
            "content": "[[IMAGE PROMPT: Figure 2]]",
            "figures": [
                {"marker": "Figure 2", "image": {"minio_key": "figures/missing.png"}}
            ],
        },
    )
    assert result["embedded_count"] == 0
    assert result["missing_images"] == ["Figure 2"]
    # Left intact so the existing sanitiser still renders a visible
    # placeholder instead of the figure silently vanishing.
    assert "[[IMAGE PROMPT: Figure 2]]" in result["content"]


@pytest.mark.asyncio
async def test_disabled_upstream_generation_is_treated_as_missing_not_an_error():
    """OpenAIImageGenerationAgent's own "disabled" backend returns a
    {generated: false, minio_key: None, ...} object — a real, expected
    state, not a template-resolution bug."""
    node = _node({"object_store": FakeObjectStore({})})

    result = await node.run(
        state={},
        resolved_config={
            "content": "[[IMAGE PROMPT: Figure 1]]",
            "figures": [
                {"marker": "Figure 1", "image": {"generated": False, "minio_key": None}}
            ],
        },
    )
    assert result["embedded_count"] == 0
    assert result["missing_images"] == ["Figure 1"]
    assert "[[IMAGE PROMPT: Figure 1]]" in result["content"]


@pytest.mark.asyncio
async def test_entirely_absent_figure_input_does_not_crash():
    """Distinct from "disabled" above: the whole figure input was never
    supplied at all (image=None, not a dict) — e.g. this workflow run
    standalone without the upstream drafts stage. Must degrade the same
    way, not raise trying to read a field off None."""
    node = _node({"object_store": FakeObjectStore({})})

    result = await node.run(
        state={},
        resolved_config={
            "content": "[[IMAGE PROMPT: Figure 1]]",
            "figures": [{"marker": "Figure 1", "image": None}],
        },
    )
    assert result["embedded_count"] == 0
    assert result["missing_images"] == ["Figure 1"]


@pytest.mark.asyncio
async def test_no_figures_configured_does_not_require_object_store():
    node = _node({"object_store": None})
    result = await node.run(
        state={},
        resolved_config={"content": "Plain text.", "figures": []},
    )
    assert result == {
        "content": "Plain text.",
        "embedded_count": 0,
        "unmatched_figures": [],
        "missing_images": [],
    }
