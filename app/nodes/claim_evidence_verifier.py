"""Verify linked proposal claims against exact, versioned source passages."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.evidence_verification import (
    relation_from_verdict,
    verify_claim_against_text,
)
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import EvidenceStance, Status
from app.proposal_graph.state import (
    proposal_graph_from_state,
    proposal_graph_state_update,
)


class ClaimEvidenceVerifierInput(BaseModel):
    pass


class ClaimEvidenceVerifierConfig(BaseModel):
    model: str = "claude-sonnet-4-5"
    minimum_support_confidence: float = Field(default=0.72, ge=0.0, le=1.0)
    max_source_characters: int = Field(default=24000, ge=1000, le=100000)


class ClaimEvidenceVerifierOutput(BaseModel):
    claims_checked: int = 0
    relations_created: int = 0
    supported_claims: int = 0
    contradicted_claims: int = 0
    unverified_claims: int = 0
    findings: list[dict[str, Any]] = Field(default_factory=list)


@NodeRegistry.register
class ClaimEvidenceVerifier(NodeType):
    type_name = "ClaimEvidenceVerifier"
    description = (
        "Verify each linked claim against an exact passage in an immutable "
        "source version."
    )
    input_schema = ClaimEvidenceVerifierInput
    config_schema = ClaimEvidenceVerifierConfig
    output_schema = ClaimEvidenceVerifierOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"llm", "cost_ledger"}

    async def _source_text(self, source, cap: int) -> tuple[str, str]:
        if source.object_key and self.services.get("object_store") is not None:
            raw = await asyncio.to_thread(
                self.services["object_store"].get_bytes,
                source.object_key,
            )
            text = (
                raw.decode("utf-8", errors="replace")
                if isinstance(raw, bytes)
                else str(raw)
            )
            return text[:cap], source.object_key
        if source.excerpt:
            return source.excerpt[:cap], source.identifier or "source excerpt"
        return "", source.identifier or "source text unavailable"

    async def run(self, state: dict, resolved_config: dict[str, Any]) -> dict:
        cfg = ClaimEvidenceVerifierConfig(**resolved_config)
        graph = proposal_graph_from_state(state)
        llm = self.services.get("llm")
        if llm is None:
            raise RuntimeError("ClaimEvidenceVerifier requires the llm service")

        relations = {}
        updated_claims = {}
        findings: list[dict[str, Any]] = []
        supported = contradicted = unverified = checked = 0

        for claim in graph.claims.values():
            if not claim.evidence_source_ids:
                continue
            checked += 1
            claim_relations = []

            for source_id in claim.evidence_source_ids:
                source = graph.evidence_sources.get(source_id)
                if source is None:
                    findings.append(
                        {
                            "claim_id": claim.id,
                            "source_id": source_id,
                            "stance": "insufficient",
                            "reason": "Referenced source does not exist.",
                        }
                    )
                    continue

                source_text, locator = await self._source_text(
                    source,
                    cfg.max_source_characters,
                )
                verdict = await verify_claim_against_text(
                    llm,
                    claim=claim.text,
                    source_text=source_text,
                    model=cfg.model,
                )
                basis = (
                    f"{claim.id}|{source.id}|{source.version_id or ''}|"
                    f"{verdict.supporting_quote}|{verdict.stance.value}"
                )
                relation_id = "EV-" + hashlib.sha256(
                    basis.encode("utf-8")
                ).hexdigest()[:16]
                relation = relation_from_verdict(
                    relation_id=relation_id,
                    claim_id=claim.id,
                    source_id=source.id,
                    source_version_id=source.version_id,
                    locator=locator,
                    verifier_model=cfg.model,
                    verdict=verdict,
                )
                relations[relation.id] = relation
                claim_relations.append(relation)
                findings.append(
                    {
                        "claim_id": claim.id,
                        "source_id": source.id,
                        "relation_id": relation.id,
                        "stance": relation.stance.value,
                        "confidence": relation.confidence,
                        "locator": relation.locator,
                        "reason": relation.reason,
                    }
                )

            strong_support = any(
                relation.stance == EvidenceStance.SUPPORTS
                and relation.confidence >= cfg.minimum_support_confidence
                for relation in claim_relations
            )
            contradiction = any(
                relation.stance == EvidenceStance.CONTRADICTS
                and relation.confidence >= cfg.minimum_support_confidence
                for relation in claim_relations
            )
            if strong_support and not contradiction:
                verification = Status.ADDRESSED
                supported += 1
            elif contradiction:
                verification = Status.PARTIAL
                contradicted += 1
            else:
                verification = Status.MISSING
                unverified += 1

            updated_claims[claim.id] = claim.model_copy(
                update={
                    "evidence_relation_ids": [
                        relation.id for relation in claim_relations
                    ],
                    "verification": verification,
                }
            )

        delta = ProposalGraph(
            claims=updated_claims,
            claim_evidence=relations,
        )
        return {
            "claims_checked": checked,
            "relations_created": len(relations),
            "supported_claims": supported,
            "contradicted_claims": contradicted,
            "unverified_claims": unverified,
            "findings": findings,
            "__state__": proposal_graph_state_update(delta),
        }
