"""Create the drafting-safe, integrity-stamped proposal truth graph."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.evidence.models import (
    EvidenceGap,
    VerifiedClaim,
    coerce_typed_list_field,
)
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.state import proposal_graph_from_state


class ProposalTruthGraphInput(BaseModel):
    pass


class ProposalTruthGraphConfig(BaseModel):
    verified_claims: str | list[VerifiedClaim]
    evidence_gaps: str | list[EvidenceGap] = Field(default_factory=list)
    blocking_issues: Any = Field(default_factory=list)
    research_manifest: Any = Field(default_factory=dict)

    @field_validator("verified_claims", mode="before")
    @classmethod
    def _coerce_verified_claims(cls, value: Any) -> Any:
        return coerce_typed_list_field(
            value,
            VerifiedClaim,
            "verified_claims",
        )

    @field_validator("evidence_gaps", mode="before")
    @classmethod
    def _coerce_evidence_gaps(cls, value: Any) -> Any:
        return coerce_typed_list_field(
            value,
            EvidenceGap,
            "evidence_gaps",
        )


class ProposalTruthGraphOutput(BaseModel):
    truth_graph: dict[str, Any] = Field(default_factory=dict)
    integrity_sha256: str
    verified_claim_ids: list[str] = Field(default_factory=list)
    qualified_claim_ids: list[str] = Field(default_factory=list)
    excluded_claim_ids: list[str] = Field(default_factory=list)
    human_review_queue: list[dict[str, Any]] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    drafting_allowed: bool = False
    approval_required: bool = True
    report: str = ""


@NodeRegistry.register
class ProposalTruthGraphAgent(NodeType):
    type_name = "ProposalTruthGraphAgent"
    description = (
        "Freeze a drafting-safe truth graph containing only verified or "
        "qualified evidence links, plus explicit gaps and approval items."
    )
    input_schema = ProposalTruthGraphInput
    config_schema = ProposalTruthGraphConfig
    output_schema = ProposalTruthGraphOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = ProposalTruthGraphConfig(**resolved_config)
        if isinstance(cfg.verified_claims, str) or isinstance(
            cfg.evidence_gaps,
            str,
        ):
            raise ValueError(
                "truth-graph evidence templates did not resolve"
            )
        graph = proposal_graph_from_state(state)
        accepted_statuses = {
            "verified",
            "verified_with_qualification",
            "internal_verified",
            "project_commitment",
        }
        accepted = {
            item.claim_id: item
            for item in cfg.verified_claims
            if item.final_status in accepted_statuses
        }
        verified_ids = sorted(
            claim_id
            for claim_id, item in accepted.items()
            if item.final_status in {"verified", "internal_verified"}
        )
        qualified_ids = sorted(
            claim_id
            for claim_id, item in accepted.items()
            if item.final_status
            in {
                "verified_with_qualification",
                "project_commitment",
            }
        )
        excluded_ids = sorted(set(graph.claims) - set(accepted))
        source_ids = {
            source_id
            for item in accepted.values()
            for source_id in item.source_ids
        }
        # ProposalEvidenceFactory stores graph relation IDs separately from
        # its package link IDs. Include relations by accepted claim as the
        # stable graph-level join.
        relations = {
            relation_id: relation.model_dump(mode="json")
            for relation_id, relation in graph.claim_evidence.items()
            if relation.claim_id in accepted
        }

        snapshot = {
            "schema_version": "eurskem-proposal-truth-graph-v1",
            "authority_order": [
                "official_call_and_template",
                "human_approved_concept_commitments",
                "verified_external_evidence",
                "approved_partner_facts",
                "unverified_research_dossiers_excluded",
            ],
            "call_requirements": {
                key: value.model_dump(mode="json")
                for key, value in graph.call_requirements.items()
            },
            "claims": {
                key: graph.claims[key].model_dump(mode="json")
                for key in sorted(accepted)
                if key in graph.claims
            },
            "verified_claim_records": {
                key: value.model_dump(mode="json")
                for key, value in accepted.items()
            },
            "evidence_sources": {
                key: graph.evidence_sources[key].model_dump(mode="json")
                for key in sorted(source_ids)
                if key in graph.evidence_sources
            },
            "claim_evidence_relations": relations,
            "concept_and_delivery_objects": {
                name: {
                    key: value.model_dump(mode="json")
                    for key, value in getattr(graph, name).items()
                }
                for name in (
                    "objectives",
                    "innovations",
                    "results",
                    "outcomes",
                    "impacts",
                    "work_packages",
                    "tasks",
                    "partners",
                    "kpis",
                    "risks",
                    "compliance",
                    "open_questions",
                )
            },
            "research_manifest": cfg.research_manifest,
            "drafting_rules": [
                "Use only claims present in truth_graph.claims for external "
                "factual assertions.",
                "Preserve qualifications attached to verified claims.",
                "Never copy facts directly from a raw Deep Research dossier.",
                "Keep missing consortium and project facts as [INPUT NEEDED].",
                "Human approval is required before the drafting stage.",
            ],
        }
        canonical = json.dumps(
            snapshot,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        blockers = _string_list(cfg.blocking_issues)
        queue = _review_queue(
            cfg.verified_claims,
            cfg.evidence_gaps,
            graph,
        )
        drafting_allowed = (
            bool(accepted)
            and not blockers
            and not any(gap.blocking for gap in cfg.evidence_gaps)
            and not any(
                item.final_status in {"mixed", "contradicted"}
                and item.materiality == "critical"
                for item in cfg.verified_claims
            )
        )
        return ProposalTruthGraphOutput(
            truth_graph=snapshot,
            integrity_sha256=digest,
            verified_claim_ids=verified_ids,
            qualified_claim_ids=qualified_ids,
            excluded_claim_ids=excluded_ids,
            human_review_queue=queue,
            evidence_gaps=cfg.evidence_gaps,
            blocking_issues=blockers,
            drafting_allowed=drafting_allowed,
            approval_required=True,
            report=(
                f"Truth graph {digest[:12]} contains {len(accepted)} "
                f"drafting-eligible claim(s) and excludes "
                f"{len(excluded_ids)} unsupported claim(s). Human approval "
                "is required before section drafting."
            ),
        ).model_dump(mode="json")


def _review_queue(
    claims: list[VerifiedClaim],
    gaps: list[EvidenceGap],
    graph: Any,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for item in claims:
        if item.human_review_required or item.final_status not in {
            "verified",
            "internal_verified",
        }:
            queue.append(
                {
                    "type": "claim",
                    "id": item.claim_id,
                    "status": item.final_status,
                    "materiality": item.materiality,
                    "decision": (
                        "approve_with_qualification"
                        if item.final_status == "verified_with_qualification"
                        else "review_or_remove"
                    ),
                }
            )
    for gap in gaps:
        queue.append(
            {
                "type": "evidence_gap",
                "id": gap.claim_id,
                "blocking": gap.blocking,
                "decision": gap.recommended_action,
                "detail": gap.gap,
            }
        )
    for collection_name in ("objectives", "kpis", "work_packages", "risks"):
        for object_id, value in getattr(graph, collection_name).items():
            status = getattr(value, "status", "review")
            queue.append(
                {
                    "type": collection_name.rstrip("s"),
                    "id": object_id,
                    "status": getattr(status, "value", str(status)),
                    "decision": "human_approval_required",
                }
            )
    return queue


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        return _string_list(parsed)
    return [str(value)]
