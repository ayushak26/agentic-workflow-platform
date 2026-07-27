"""
ProposalGraph — the container that lives inside WorkflowState under one key.

Why a container (not 15 separate state keys)
--------------------------------------------
- One additive key `proposal_graph` keeps the WorkflowState change minimal and
  keeps existing node/test contracts untouched.
- Parallel drafters (LangGraph fan-out) each contribute objects. The reducer
  `merge_graph` must be associative and lossless so concurrent writes from the
  five section drafters don't clobber each other — same guarantee your existing
  `merge_node_outputs` reducer gives for node outputs.

Merge semantics
---------------
Each object type is stored as a dict keyed by object id. Merging two graphs
unions the dicts per type; on an id collision the RIGHT (later) write wins for
that object, but a partial object never erases a fuller one silently — we merge
field-by-field, preferring non-null values. This means two drafters can each
fill different fields of the same WorkPackage and both survive.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import (
    CallRequirement, Claim, ComplianceObject, ConceptAlternative,
    EvidenceRelation, EvidenceSource, Impact, Innovation, KPI, Objective,
    OpenQuestion, Outcome, Partner, Result, Risk, Task, WorkPackage,
)

# type name -> (collection attribute, model class)
_COLLECTIONS: dict[str, type[BaseModel]] = {
    "call_requirements": CallRequirement,
    "claims": Claim,
    "evidence_sources": EvidenceSource,
    "claim_evidence": EvidenceRelation,
    "concept_alternatives": ConceptAlternative,
    "objectives": Objective,
    "innovations": Innovation,
    "results": Result,
    "outcomes": Outcome,
    "impacts": Impact,
    "work_packages": WorkPackage,
    "tasks": Task,
    "partners": Partner,
    "kpis": KPI,
    "risks": Risk,
    "compliance": ComplianceObject,
    "open_questions": OpenQuestion,
}


class ProposalGraph(BaseModel):
    """All 15 object types, each as an id-keyed dict."""
    call_requirements: dict[str, CallRequirement] = Field(default_factory=dict)
    claims: dict[str, Claim] = Field(default_factory=dict)
    evidence_sources: dict[str, EvidenceSource] = Field(default_factory=dict)
    claim_evidence: dict[str, EvidenceRelation] = Field(default_factory=dict)
    concept_alternatives: dict[str, ConceptAlternative] = Field(default_factory=dict)
    objectives: dict[str, Objective] = Field(default_factory=dict)
    innovations: dict[str, Innovation] = Field(default_factory=dict)
    results: dict[str, Result] = Field(default_factory=dict)
    outcomes: dict[str, Outcome] = Field(default_factory=dict)
    impacts: dict[str, Impact] = Field(default_factory=dict)
    work_packages: dict[str, WorkPackage] = Field(default_factory=dict)
    tasks: dict[str, Task] = Field(default_factory=dict)
    partners: dict[str, Partner] = Field(default_factory=dict)
    kpis: dict[str, KPI] = Field(default_factory=dict)
    risks: dict[str, Risk] = Field(default_factory=dict)
    compliance: dict[str, ComplianceObject] = Field(default_factory=dict)
    open_questions: dict[str, OpenQuestion] = Field(default_factory=dict)


def _merge_object(left: BaseModel, right: BaseModel) -> BaseModel:
    """Field-by-field merge; right wins only where it has a non-null/non-empty
    value. Prevents a partial write from erasing a fuller earlier one."""
    merged = left.model_dump()
    for key, rval in right.model_dump().items():
        if rval in (None, "", [], {}):
            continue
        merged[key] = rval
    return left.__class__(**merged)


def merge_graph(left: Any, right: Any) -> dict:
    """Reducer for the `proposal_graph` state key. Associative + lossless.

    Accepts ProposalGraph or dicts (LangGraph may hand back serialised state),
    so it is safe as an Annotated reducer regardless of serialisation.
    """
    lg = left if isinstance(left, ProposalGraph) else ProposalGraph(**(left or {}))
    rg = right if isinstance(right, ProposalGraph) else ProposalGraph(**(right or {}))

    out = ProposalGraph()
    for coll in _COLLECTIONS:
        merged = dict(getattr(lg, coll))          # copy left
        for obj_id, robj in getattr(rg, coll).items():
            if obj_id in merged:
                merged[obj_id] = _merge_object(merged[obj_id], robj)
            else:
                merged[obj_id] = robj
        setattr(out, coll, merged)
    return out.model_dump()


def empty_graph() -> ProposalGraph:
    return ProposalGraph()
