"""Tiered structural smoke tests for the web-search / image-generation /
vision / figure-embedding integration.

Zero tokens, no live API calls (compile_graph=True dry-compiles the LangGraph
without running a node) — meant as a fast "is this still wired up correctly"
check to run first when something in this area breaks, and as small,
readable reference examples: the real Horizon Part B files are too large to
skim just to see how WebSearchAgent/KimiVisionAgent/
OpenAIImageGenerationAgent/FigureEmbedder fit together.

- simple: one new integration alone (the existing demo workflows).
- medium: several new integrations chained (web search -> synthesis that
  emits a figure marker -> image generation -> figure embedding).
- complex: the real, shipped multi-stage Horizon Part B workflows, standalone
  and as the staged pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.pipeline_preflight import preflight_pipeline_yaml
from app.runtime.preflight import preflight_workflow_yaml

# ---- simple: one new integration alone -------------------------------------

SIMPLE_WORKFLOWS = [
    Path("workflows/web_search_agent_demo.yaml"),
    Path("workflows/kimi_vision_agent_demo.yaml"),
]


@pytest.mark.parametrize("path", SIMPLE_WORKFLOWS, ids=lambda p: p.stem)
def test_simple_tier_single_integration_demo_passes_preflight(path: Path):
    report = preflight_workflow_yaml(path.read_text(), compile_graph=True)
    assert report.valid, [i.message for i in report.errors]
    assert report.tokens_spent == 0


# ---- medium: several new integrations chained together --------------------

MEDIUM_WORKFLOW = """
name: Medium tier smoke test - web search to embedded figure
description: >-
  Exercises web search feeding a synthesis step, an image generated from
  that synthesis, and FigureEmbedder splicing it into the final text - the
  same [[IMAGE PROMPT: marker]] pattern the Horizon Part B workflows use to
  get a real image into a rendered document, just small enough to read in
  one screen.
inputs:
  topic:
    type: text
    required: true

nodes:
  - id: web_search
    type: WebSearchAgent
    config:
      query: "{{inputs.topic}}"
      provider: auto
      top_k: 5

  - id: synthesis
    type: TransformAgent
    config:
      model: claude-sonnet-4-5
      max_tokens: 2000
      prompt_template: |
        Summarise this web search context in two sentences. Where a diagram
        would help, insert exactly this marker on its own line, unmodified:
        [[IMAGE PROMPT: Overview Diagram]]

        RESULTS:
        {{web_search.results}}

  - id: generate_figure
    type: OpenAIImageGenerationAgent
    config:
      backend: openai
      prompt: "A simple flat-vector overview diagram, based on: {{synthesis.raw}}"

  - id: embed_figure
    type: FigureEmbedder
    config:
      content: "{{synthesis.raw}}"
      figures:
        - marker: "Overview Diagram"
          image: "{{generate_figure}}"

edges:
  - from: web_search
    to: synthesis
  - from: synthesis
    to: generate_figure
  - from: generate_figure
    to: embed_figure

entry: web_search
exit: embed_figure
"""


def test_medium_tier_multi_integration_chain_passes_preflight():
    report = preflight_workflow_yaml(MEDIUM_WORKFLOW, compile_graph=True)
    assert report.valid, [i.message for i in report.errors]
    assert report.tokens_spent == 0


# ---- complex: the real, shipped Horizon Part B workflows -------------------

COMPLEX_WORKFLOWS = [
    Path("workflows/horizon_partb_autonomous_docx.yaml"),
    Path("workflows/horizon_partb_backhalf.yaml"),
    Path("workflows/horizon_partb_evidence.yaml"),
    Path("workflows/horizon_partb_drafts.yaml"),
    Path("workflows/horizon_partb_drafts_to_docx.yaml"),
    Path("workflows/horizon_proposal_hitl_pdf.yaml"),
    Path("workflows/horizon_partb_backhalf_v2.yaml"),
]


@pytest.mark.parametrize("path", COMPLEX_WORKFLOWS, ids=lambda p: p.stem)
def test_complex_tier_full_horizon_workflow_passes_preflight(path: Path):
    report = preflight_workflow_yaml(path.read_text(), compile_graph=True)
    assert report.valid, [i.message for i in report.errors]
    assert report.tokens_spent == 0


def test_complex_tier_staged_pipeline_resolves_every_cross_stage_input():
    path = Path("workflows/pipelines/horizon_partb.pipeline.yaml")
    report = preflight_pipeline_yaml(path.read_text())
    assert report.valid, [i.message for i in report.errors]
