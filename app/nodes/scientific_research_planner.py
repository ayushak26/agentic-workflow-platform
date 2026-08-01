"""Proposal-aware research planning guided by curated scientific skills."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.evidence.retrieval import stable_id
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.state import proposal_graph_from_state
from app.research.deep_research import ResearchBrief, ResearchTrack


class _ResearchBriefDraft(BaseModel):
    track: ResearchTrack
    question: str
    purpose: str
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_call_requirement_ids: list[str] = Field(default_factory=list)
    required_source_types: list[str] = Field(default_factory=list)
    geographic_scope: list[str] = Field(default_factory=lambda: ["European Union"])
    date_priority: str = "2021-present"
    must_find: list[str] = Field(default_factory=list)
    tier: Literal["standard", "critical"] = "standard"


class _ResearchPlanDraft(BaseModel):
    briefs: list[_ResearchBriefDraft] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class ScientificResearchPlannerInput(BaseModel):
    pass


class ScientificResearchPlannerConfig(BaseModel):
    call_context: Any = ""
    concept_context: Any = ""
    model: str = "gpt-5.6-terra"
    max_briefs: int = Field(default=8, ge=1, le=12)
    max_total_tool_calls: int = Field(default=72, ge=4, le=160)
    standard_tool_calls: int = Field(default=7, ge=2, le=25)
    critical_tool_calls: int = Field(default=11, ge=2, le=40)
    standard_research_model: Literal[
        "gpt-5.6-sol",
        "claude-fable-5",
    ] = "gpt-5.6-sol"
    critical_research_model: Literal[
        "gpt-5.6-sol",
        "claude-fable-5",
    ] = "gpt-5.6-sol"
    max_skills_per_brief: int = Field(default=4, ge=1, le=5)


class ScientificResearchPlannerOutput(BaseModel):
    research_briefs: list[ResearchBrief] = Field(default_factory=list)
    brief_count: int = 0
    total_tool_call_budget: int = 0
    skills_manifest: dict[str, list[str]] = Field(default_factory=dict)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    governance_rules: list[str] = Field(default_factory=list)


_TRACK_GUIDANCE = {
    "state_of_art": (
        "literature-review research-lookup citation-management"
    ),
    "eu_policy_and_regulation": "research-lookup database-lookup",
    "prior_projects_and_synergies": "research-lookup citation-management",
    "methodology_selection": (
        "scientific-brainstorming scientific-critical-thinking methodology"
    ),
    "market_adoption_and_social": (
        "research-lookup database-lookup adoption social evidence"
    ),
    "environment_climate_biodiversity": (
        "research-lookup database-lookup geomaster geopandas"
    ),
    "impact_baselines_and_targets": (
        "database-lookup scientific-critical-thinking quantitative evidence"
    ),
    "risks_contradictions_and_failure_conditions": (
        "scientific-critical-thinking peer-review contradictory evidence"
    ),
}


@NodeRegistry.register
class ScientificResearchPlannerAgent(NodeType):
    type_name = "ScientificResearchPlannerAgent"
    description = (
        "Turn the call, selected concept, and proposal graph into several "
        "bounded research briefs, each routed through approved K-Dense skills."
    )
    input_schema = ScientificResearchPlannerInput
    config_schema = ScientificResearchPlannerConfig
    output_schema = ScientificResearchPlannerOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = ScientificResearchPlannerConfig(**resolved_config)
        graph = proposal_graph_from_state(state)
        llm = self.services.get("llm")
        catalog = self.services.get("scientific_skill_catalog")
        if llm is None or catalog is None:
            missing = [
                name
                for name, service in (
                    ("llm", llm),
                    ("scientific_skill_catalog", catalog),
                )
                if service is None
            ]
            raise RuntimeError(
                f"ScientificResearchPlannerAgent requires services {missing}"
            )
        if not graph.claims:
            raise ValueError(
                "The proposal graph has no atomic claims. Run GraphNormalizer "
                "before research planning."
            )

        draft = await llm.complete_structured(
            model=cfg.model,
            system=(
                "Create a bounded evidence-research plan for a Horizon Europe "
                "Part B proposal. Return focused questions, not a generic "
                "'research everything' instruction. Cover only tracks that "
                "materially apply, prioritising state of the art, EU policy, "
                "prior projects, methodology, adoption, environmental effects, "
                "impact baselines, and contradictory/failure evidence. Every "
                "brief must link to real claim IDs and, where relevant, call "
                "requirement IDs from the supplied graph. Require supporting "
                "and contradictory evidence, limitations, geographic fit, and "
                "dates/units for quantitative claims. Deep Research may "
                "propose evidence or methodological options but must never "
                "change objectives, KPIs, work packages, partners, budgets, or "
                "the authoritative call interpretation."
            ),
            user=(
                "PROPOSAL GRAPH:\n"
                + json.dumps(
                    graph.model_dump(mode="json"),
                    ensure_ascii=False,
                )
                + "\n\nCALL CONTEXT:\n"
                + _json_text(cfg.call_context)
                + "\n\nSELECTED CONCEPT CONTEXT:\n"
                + _json_text(cfg.concept_context)
                + f"\n\nCreate at most {cfg.max_briefs} briefs."
            ),
            response_model=_ResearchPlanDraft,
            temperature=0.0,
            max_tokens=12000,
        )

        known_claims = set(graph.claims)
        known_requirements = set(graph.call_requirements)
        briefs: list[ResearchBrief] = []
        skills_manifest: dict[str, list[str]] = {}
        versions: dict[str, str] = {}
        remaining_budget = cfg.max_total_tool_calls

        for item in draft.briefs[: cfg.max_briefs]:
            claim_ids = [
                claim_id
                for claim_id in dict.fromkeys(item.linked_claim_ids)
                if claim_id in known_claims
            ]
            if not claim_ids:
                continue
            requirement_ids = [
                requirement_id
                for requirement_id in dict.fromkeys(
                    item.linked_call_requirement_ids
                )
                if requirement_id in known_requirements
            ]
            requested_calls = (
                cfg.critical_tool_calls
                if item.tier == "critical"
                else cfg.standard_tool_calls
            )
            if remaining_budget < 2:
                break
            tool_calls = min(requested_calls, remaining_budget)
            guidance = _TRACK_GUIDANCE.get(item.track, "")
            objective = f"{item.question}\n{guidance}"
            # The track guidance names specific skills as the intended fit
            # for that research track. Request them explicitly rather than
            # relying only on auto-select scoring against every allowlisted
            # skill — a good scoring match is likely but not guaranteed, and
            # a track that names a skill should always get it if it loaded.
            requested_skills = tuple(
                dict.fromkeys(
                    token
                    for token in guidance.split()
                    if token in catalog.loaded_skill_names
                )
            )
            selection = catalog.select(
                objective=objective,
                requested=requested_skills,
                auto_select=True,
                max_skills=cfg.max_skills_per_brief,
            )
            brief_id = stable_id(
                "RQ",
                item.track,
                item.question,
                ",".join(claim_ids),
                length=12,
            )
            research_model = (
                cfg.critical_research_model
                if item.tier == "critical"
                else cfg.standard_research_model
            )
            brief = ResearchBrief(
                brief_id=brief_id,
                track=item.track,
                question=item.question.strip(),
                purpose=item.purpose.strip(),
                linked_claim_ids=claim_ids,
                linked_call_requirement_ids=requirement_ids,
                required_source_types=(
                    item.required_source_types
                    or [
                        "official_eu",
                        "peer_reviewed_primary",
                        "recognised_standard_or_dataset",
                    ]
                ),
                geographic_scope=item.geographic_scope or ["European Union"],
                date_priority=item.date_priority or "2021-present",
                must_find=(
                    item.must_find
                    or [
                        "supporting evidence",
                        "contradictory or null evidence",
                        "methodological limitations",
                        "validation precedent",
                    ]
                ),
                selected_skills=selection.names,
                tier=item.tier,
                research_model=research_model,
                max_tool_calls=tool_calls,
            )
            briefs.append(brief)
            skills_manifest[brief_id] = selection.names
            versions.update(selection.versions)
            remaining_budget -= tool_calls

        if not briefs:
            raise ValueError(
                "Research planning produced no brief linked to a known "
                "proposal claim."
            )

        return ScientificResearchPlannerOutput(
            research_briefs=briefs,
            brief_count=len(briefs),
            total_tool_call_budget=sum(
                brief.max_tool_calls for brief in briefs
            ),
            skills_manifest=skills_manifest,
            skill_versions=versions,
            unresolved_questions=draft.unresolved_questions,
            governance_rules=[
                "The official call outranks every research source.",
                "Deep Research dossiers are candidate material, not evidence.",
                "Only exact-passage-verified claims enter the truth graph.",
                "Objectives, KPIs, methods, work packages, partners, and "
                "budgets require governed workflow and human approval.",
            ],
        ).model_dump(mode="json")


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
