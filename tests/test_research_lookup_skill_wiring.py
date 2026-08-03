"""Phase 1 coverage for the research-lookup skill guiding paper-search-mcp
query planning, and for the deep-research citation-attribution step.

The research-lookup skill was previously inert: its markdown was only ever
injected into ScientificSkillAgent's own prompt (which makes no external
call), and its bundled research_lookup.py script was never invoked anywhere.
Its search methodology now shapes the queries ScholarlyCandidateDiscoveryAgent
actually sends to paper-search-mcp.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.nodes.evidence_agent import ScholarlyCandidateDiscoveryConfig
from app.nodes.registry import NodeRegistry
from app.research.deep_research import (
    BoundedToolResearchService,
    ResearchBrief,
    ResearchCitation,
)
from app.research.skills import ScientificSkillCatalog

SKILLS_ROOT = Path("scientific-agent-skills/skills")


def _node(services: dict):
    cls = NodeRegistry.get("ScholarlyCandidateDiscoveryAgent")
    return cls("discover", {}, services=services)


@pytest.fixture()
def catalog() -> ScientificSkillCatalog:
    cat = ScientificSkillCatalog(
        SKILLS_ROOT, allowlist=("research-lookup",), enabled=True
    )
    cat.refresh()
    return cat


class TestSkillGuidanceInQueryPlanner:
    def test_real_research_lookup_skill_loads_and_is_injected(self, catalog):
        if "research-lookup" not in catalog.loaded_skill_names:
            pytest.skip("vendored research-lookup skill not installed")
        node = _node({"scientific_skill_catalog": catalog})
        guidance = node._query_planning_guidance(
            ScholarlyCandidateDiscoveryConfig()
        )
        assert guidance
        assert "research-lookup" in guidance

    def test_guidance_is_truncated_to_the_configured_budget(self, catalog):
        if "research-lookup" not in catalog.loaded_skill_names:
            pytest.skip("vendored research-lookup skill not installed")
        node = _node({"scientific_skill_catalog": catalog})
        guidance = node._query_planning_guidance(
            ScholarlyCandidateDiscoveryConfig(max_skill_prompt_chars=500)
        )
        assert len(guidance) <= 500

    def test_missing_catalog_degrades_silently(self):
        """A disabled/absent skill catalog must never fail discovery."""
        node = _node({})
        assert node._query_planning_guidance(
            ScholarlyCandidateDiscoveryConfig()
        ) == ""

    def test_unapproved_skill_degrades_silently(self):
        cat = ScientificSkillCatalog(
            SKILLS_ROOT, allowlist=("peer-review",), enabled=True
        )
        cat.refresh()
        node = _node({"scientific_skill_catalog": cat})
        assert node._query_planning_guidance(
            ScholarlyCandidateDiscoveryConfig()
        ) == ""

    def test_explicitly_disabled_via_config(self, catalog):
        node = _node({"scientific_skill_catalog": catalog})
        assert node._query_planning_guidance(
            ScholarlyCandidateDiscoveryConfig(query_planning_skill=None)
        ) == ""
        assert node._query_planning_guidance(
            ScholarlyCandidateDiscoveryConfig(max_skill_prompt_chars=0)
        ) == ""


class _AttributionLLM:
    """Returns a fixed attribution set, recording what it was asked."""

    def __init__(self, attributions: list[dict]):
        self.attributions = attributions
        self.calls: list[dict] = []

    async def complete_structured(self, *, model, response_model, **kwargs):
        self.calls.append(kwargs)
        return response_model(attributions=self.attributions)


class _FailingLLM:
    async def complete_structured(self, **kwargs):
        raise RuntimeError("structured output unsupported")


class TestCitationAttributionStep:
    @staticmethod
    def _brief() -> ResearchBrief:
        return ResearchBrief(
            brief_id="RB-1",
            track="state_of_art",
            question="Q",
            purpose="p",
            linked_claim_ids=["CL-1", "CL-2"],
        )

    @staticmethod
    def _citations() -> list[ResearchCitation]:
        return [
            ResearchCitation(citation_id="c1", title="T1", url="https://a/1"),
            ResearchCitation(citation_id="c2", title="T2", url="https://a/2"),
        ]

    async def test_attributes_each_citation_to_one_claim(self):
        llm = _AttributionLLM(
            [
                {"citation_id": "c1", "claim_id": "CL-1", "stance": "supports"},
                {"citation_id": "c2", "claim_id": "CL-2", "stance": "contradicts"},
            ]
        )
        service = BoundedToolResearchService(llm=llm, web_search=object())
        citations = self._citations()
        await service._attribute_citations(self._brief(), citations, 10.0)
        assert citations[0].claim_id == "CL-1"
        assert citations[0].stance == "supports"
        assert citations[1].claim_id == "CL-2"
        assert citations[1].stance == "contradicts"

    async def test_unknown_claim_id_is_rejected_not_trusted(self):
        """A hallucinated claim id must never be written onto a citation."""
        llm = _AttributionLLM(
            [{"citation_id": "c1", "claim_id": "CL-DOES-NOT-EXIST",
              "stance": "supports"}]
        )
        service = BoundedToolResearchService(llm=llm, web_search=object())
        citations = self._citations()
        await service._attribute_citations(self._brief(), citations, 10.0)
        assert citations[0].claim_id is None

    async def test_unknown_citation_id_is_ignored(self):
        llm = _AttributionLLM(
            [{"citation_id": "nope", "claim_id": "CL-1", "stance": "supports"}]
        )
        service = BoundedToolResearchService(llm=llm, web_search=object())
        citations = self._citations()
        await service._attribute_citations(self._brief(), citations, 10.0)
        assert all(c.claim_id is None for c in citations)

    async def test_omitted_citations_stay_unattributed(self):
        """Omission is the model's safe escape hatch for "does not clearly
        speak to any one claim" — it must not become a default attribution."""
        llm = _AttributionLLM(
            [{"citation_id": "c1", "claim_id": "CL-1", "stance": "supports"}]
        )
        service = BoundedToolResearchService(llm=llm, web_search=object())
        citations = self._citations()
        await service._attribute_citations(self._brief(), citations, 10.0)
        assert citations[0].claim_id == "CL-1"
        assert citations[1].claim_id is None

    async def test_llm_failure_leaves_citations_unattributed(self):
        service = BoundedToolResearchService(
            llm=_FailingLLM(), web_search=object()
        )
        citations = self._citations()
        await service._attribute_citations(self._brief(), citations, 10.0)
        assert all(c.claim_id is None for c in citations)

    async def test_cost_cap_skips_attribution_entirely(self):
        llm = _AttributionLLM(
            [{"citation_id": "c1", "claim_id": "CL-1", "stance": "supports"}]
        )
        service = BoundedToolResearchService(llm=llm, web_search=object())
        citations = self._citations()
        await service._attribute_citations(self._brief(), citations, 0.0)
        assert llm.calls == []
        assert all(c.claim_id is None for c in citations)
