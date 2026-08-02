"""Produce one structured Method Card per frozen-concept objective.

Runs only after the concept freeze (Human Gate 3) and after the truth graph
is populated, so methodology choices are grounded in objectives that have
already survived human approval — not drafted speculatively alongside
concept alternatives. Domain scientific skills (geomaster/geopandas for
territorial methods, pymoo for trade-off optimisation, networkx for
actor/value-chain relationships, hypothesis-generation for falsifiable
questions, statistical-analysis for validation design) are auto-selected per
objective from the approved catalog and only ever consumed as prose
guidance — never as tool-executing instructions.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.evidence.retrieval import stable_id
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.state import proposal_graph_from_state
from app.research.deep_research import ResearchBrief


class MethodologyEngineeringInput(BaseModel):
    pass


class MethodologyEngineeringConfig(BaseModel):
    model: str = "claude-opus-5"
    research_briefs: str | list[ResearchBrief] = Field(default_factory=list)
    # Free-form: the frozen ConceptAlternative dict from ConceptFreezeAgent,
    # used only as prose grounding context, never parsed into a typed model
    # here (methodology engineering must still work if this is omitted).
    selected_concept: Any = None
    max_objectives: int = Field(default=10, ge=1, le=20)
    max_skills_per_card: int = Field(default=2, ge=1, le=4)


class _MethodCardDraft(BaseModel):
    research_question_id: str = ""
    inputs: list[str] = Field(default_factory=list)
    method: str
    baseline_or_comparator: str = ""
    validation: str = ""
    uncertainty_method: str = ""
    failure_condition: str = ""
    responsible_capability: str = ""
    evidence_claim_ids: list[str] = Field(default_factory=list)
    status: Literal[
        "drafted",
        "information_required",
        "needs_review",
    ] = "drafted"


class MethodCard(BaseModel):
    method_id: str
    supports_objective: str
    research_question_id: str = ""
    inputs: list[str] = Field(default_factory=list)
    method: str
    baseline_or_comparator: str = ""
    validation: str = ""
    uncertainty_method: str = ""
    failure_condition: str = ""
    responsible_capability: str = ""
    evidence_claim_ids: list[str] = Field(default_factory=list)
    status: Literal[
        "drafted",
        "information_required",
        "needs_review",
    ] = "drafted"
    selected_skills: list[str] = Field(default_factory=list)


class MethodologyEngineeringOutput(BaseModel):
    method_cards: list[MethodCard] = Field(default_factory=list)
    objectives_processed: int = 0
    skills_manifest: dict[str, list[str]] = Field(default_factory=dict)
    skill_versions: dict[str, str] = Field(default_factory=dict)


@NodeRegistry.register
class MethodologyEngineeringAgent(NodeType):
    type_name = "MethodologyEngineeringAgent"
    description = (
        "Produce one skill-guided Method Card per frozen-concept objective: "
        "method, baseline, validation, uncertainty handling, and failure "
        "condition, grounded only in verified claims and known research "
        "questions."
    )
    input_schema = MethodologyEngineeringInput
    config_schema = MethodologyEngineeringConfig
    output_schema = MethodologyEngineeringOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"llm", "cost_ledger"}

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = MethodologyEngineeringConfig(**resolved_config)
        if isinstance(cfg.research_briefs, str):
            raise ValueError(
                "research_briefs template did not resolve to a list"
            )
        llm = self.services.get("llm")
        if llm is None:
            raise RuntimeError(
                "MethodologyEngineeringAgent requires the llm service"
            )
        catalog = self.services.get("scientific_skill_catalog")

        graph = proposal_graph_from_state(state)
        if not graph.objectives:
            raise ValueError(
                "The proposal graph has no objectives. Freeze a concept and "
                "populate objectives before methodology engineering."
            )

        known_brief_ids = {brief.brief_id for brief in cfg.research_briefs}
        known_claim_ids = set(graph.claims)
        objectives = list(graph.objectives.values())[: cfg.max_objectives]

        cards = await asyncio.gather(
            *(
                self._draft_one_card(
                    llm=llm,
                    catalog=catalog,
                    objective_id=objective.id,
                    objective_text=objective.text,
                    model=cfg.model,
                    max_skills=cfg.max_skills_per_card,
                    known_brief_ids=known_brief_ids,
                    known_claim_ids=known_claim_ids,
                    selected_concept=cfg.selected_concept,
                )
                for objective in objectives
            )
        )

        manifest = {card.method_id: card.selected_skills for card in cards}
        return MethodologyEngineeringOutput(
            method_cards=cards,
            objectives_processed=len(cards),
            skills_manifest=manifest,
        ).model_dump(mode="json")

    async def _draft_one_card(
        self,
        *,
        llm: Any,
        catalog: Any,
        objective_id: str,
        objective_text: str,
        model: str,
        max_skills: int,
        known_brief_ids: set[str],
        known_claim_ids: set[str],
        selected_concept: Any = None,
    ) -> MethodCard:
        selected_names: list[str] = []
        guidance = ""
        if catalog is not None and getattr(catalog, "enabled", False):
            try:
                selection = catalog.select(
                    objective=objective_text,
                    auto_select=True,
                    max_skills=max_skills,
                )
                selected_names = selection.names
                guidance = catalog.prompt_bundle(selection)
            except Exception:
                selected_names = []
                guidance = ""

        system = (
            "You are a Horizon Europe methodology architect. Produce ONE "
            "Method Card for the supplied objective: a concrete method, its "
            "baseline or comparator, how it will be validated, how "
            "uncertainty is handled, and the explicit condition under which "
            "the method should be judged to have failed. Use only IDs "
            "supplied to you — never invent a research-question ID or "
            "evidence-claim ID. If a field cannot be determined from the "
            "supplied facts, set status to 'information_required' and leave "
            "it as an honest placeholder rather than fabricating detail."
        )
        if guidance:
            system += (
                "\n\nAPPROVED SCIENTIFIC SKILL GUIDANCE (methodology only, "
                f"not permission to bypass tools):\n{guidance}"
            )
        user = (
            f"OBJECTIVE {objective_id}:\n{objective_text}\n\n"
            "KNOWN RESEARCH QUESTION IDS (pick at most one, or leave "
            "research_question_id empty):\n"
            + ", ".join(sorted(known_brief_ids)) + "\n\n"
            "KNOWN EVIDENCE CLAIM IDS (evidence_claim_ids must be a subset "
            "of these):\n"
            + ", ".join(sorted(known_claim_ids))
        )
        if selected_concept:
            user += (
                "\n\nFROZEN CONCEPT (approved posture — ground the method in "
                f"this scope, do not exceed it):\n{selected_concept}"
            )
        draft = await llm.complete_structured(
            model=model,
            system=system,
            user=user,
            response_model=_MethodCardDraft,
            temperature=0.0,
            max_tokens=2500,
        )

        return MethodCard(
            method_id=stable_id("M", objective_id, objective_text),
            supports_objective=objective_id,
            research_question_id=(
                draft.research_question_id
                if draft.research_question_id in known_brief_ids
                else ""
            ),
            inputs=draft.inputs,
            method=draft.method,
            baseline_or_comparator=draft.baseline_or_comparator,
            validation=draft.validation,
            uncertainty_method=draft.uncertainty_method,
            failure_condition=draft.failure_condition,
            responsible_capability=draft.responsible_capability,
            evidence_claim_ids=[
                claim_id
                for claim_id in dict.fromkeys(draft.evidence_claim_ids)
                if claim_id in known_claim_ids
            ],
            status=draft.status,
            selected_skills=selected_names,
        )
