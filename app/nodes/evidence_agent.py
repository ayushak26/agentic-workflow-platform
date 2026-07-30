"""Scholarly candidate discovery for proposal claims.

Important boundary:
    Search metadata is a candidate set, not evidence.

This node never changes ``Claim.verification`` and never writes candidate IDs
to ``Claim.evidence_source_ids``. Full text must be fetched and an exact
passage must pass the separate ProposalEvidenceFactoryAgent before a proposal
drafter may use a citation.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from app.evidence.models import CandidateSource, SearchAuditRecord
from app.evidence.retrieval import (
    candidate_from_paper,
    deduplicate_candidates,
    papers_from_payload,
    utc_now,
)
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.models import Claim
from app.proposal_graph.state import proposal_graph_from_state


_DEFAULT_SOURCES = [
    "arxiv",
    "openalex",
    "europepmc",
    "core",
    "openaire",
    "zenodo",
    "hal",
    "doaj",
    "pmc",
    "semantic"
]


class ClaimSearchPlan(BaseModel):
    discovery_queries: list[str] = Field(default_factory=list, max_length=4)
    contradiction_queries: list[str] = Field(default_factory=list, max_length=2)


_BOOLEAN_QUERY_CHARS = str.maketrans({c: " " for c in "()\"'"})


def _sanitize_query(text: str) -> str:
    """Strip boolean/advanced-search syntax from a generated query.

    Several downstream providers (OpenAIRE's search API in particular) only
    accept plain keywords: quoted phrases and parenthesized AND/OR groups
    either 400 outright or get mis-tokenized by their query parser. The
    system prompt already asks the model for plain keywords, but this is a
    second, unconditional line of defense in case it doesn't comply.
    """
    cleaned = text.translate(_BOOLEAN_QUERY_CHARS)
    for op in (" AND ", " OR ", " and ", " or "):
        cleaned = cleaned.replace(op, " ")
    return " ".join(cleaned.split())


class ScholarlyCandidateDiscoveryInput(BaseModel):
    pass


class ScholarlyCandidateDiscoveryConfig(BaseModel):
    mcp_server: str = "paper-search-mcp"
    tool: str = "search_papers"
    sources: list[str] = Field(default_factory=lambda: list(_DEFAULT_SOURCES))
    max_results_per_source: int = Field(default=2, ge=1, le=10)
    max_candidates_per_claim: int = Field(default=8, ge=1, le=30)
    max_claims: int = Field(default=20, ge=1, le=100)
    claim_types: list[str] = Field(
        default_factory=lambda: [
            "state_of_art",
            "impact",
            "problem",
            "method",
        ]
    )
    require_contradiction_search: bool = True
    model: str | None = "claude-sonnet-4-5"
    # Hard wall-clock ceiling for the whole discovery loop (all claims x all
    # queries). Each individual MCP call already has its own per-call
    # timeout (mcp_tool_timeout_seconds), but nothing previously bounded the
    # loop as a whole — up to max_claims x 6 queries, each allowed up to that
    # per-call timeout, could run for hours. Once this elapses, discovery
    # stops claiming new work and hands off whatever candidates it already
    # found, rather than blocking the rest of the workflow indefinitely.
    max_duration_seconds: float = Field(default=1800.0, gt=0)


class ScholarlyCandidateDiscoveryOutput(BaseModel):
    candidates_found: int = 0
    claims_searched: int = 0
    candidates: list[CandidateSource] = Field(default_factory=list)
    search_audit: list[SearchAuditRecord] = Field(default_factory=list)
    report: str = ""
    # True when max_duration_seconds was hit and the loop handed off early
    # with a partial candidate set, rather than searching every claim.
    timed_out: bool = False
    # Backwards-compatible display fields. Candidate discovery does not add
    # verified sources or link claims, so these remain zero by design.
    sources_added: int = 0
    claims_linked: int = 0
    sources: list[dict[str, Any]] = Field(default_factory=list)


class _ScholarlyCandidateDiscovery(NodeType):
    input_schema = ScholarlyCandidateDiscoveryInput
    config_schema = ScholarlyCandidateDiscoveryConfig
    output_schema = ScholarlyCandidateDiscoveryOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = ScholarlyCandidateDiscoveryConfig(**resolved_config)
        graph = proposal_graph_from_state(state)
        llm = self.services.get("llm")
        mcp = self.services.get("mcp_client")
        if llm is None or mcp is None:
            missing = [
                name
                for name, service in (("llm", llm), ("mcp_client", mcp))
                if service is None
            ]
            raise RuntimeError(
                f"{self.type_name} requires services {missing}"
            )

        wanted = set(cfg.claim_types)
        targets = [
            claim
            for claim in graph.claims.values()
            if claim.claim_type in wanted and not claim.evidence_relation_ids
        ][: cfg.max_claims]

        candidates: list[CandidateSource] = []
        audit: list[SearchAuditRecord] = []
        report_lines: list[str] = []
        sources_arg = ",".join(cfg.sources)
        deadline = time.monotonic() + cfg.max_duration_seconds
        claims_processed = 0
        timed_out = False

        for claim in targets:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            claims_processed += 1
            plan = await self._search_plan(llm, claim, cfg.model)
            queries = [
                ("discovery", query)
                for query in plan.discovery_queries[:4]
            ]
            if cfg.require_contradiction_search:
                queries.extend(
                    ("contradiction", query)
                    for query in plan.contradiction_queries[:2]
                )

            claim_candidates: list[CandidateSource] = []
            for purpose, query in queries:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                searched_at = utc_now()
                error: str | None = None
                papers: list[dict[str, Any]] = []
                try:
                    raw = await mcp.call_tool(
                        name=cfg.tool,
                        arguments={
                            "query": query,
                            "sources": sources_arg,
                            "max_results_per_source": (
                                cfg.max_results_per_source
                            ),
                        },
                        server=cfg.mcp_server,
                    )
                    papers = papers_from_payload(raw)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"[:500]

                audit.append(
                    SearchAuditRecord(
                        claim_id=claim.id,
                        query=query,
                        source_or_database=sources_arg,
                        filters={
                            "max_results_per_source": (
                                cfg.max_results_per_source
                            )
                        },
                        searched_at=searched_at,
                        result_count=len(papers),
                        purpose=purpose,
                        error=error,
                    )
                )
                if error:
                    report_lines.append(
                        f"[{claim.id}] {purpose} search failed: {error}"
                    )
                    continue
                for paper in papers:
                    claim_candidates.append(
                        candidate_from_paper(
                            paper,
                            claim_id=claim.id,
                            query=query,
                            purpose=purpose,
                        )
                    )

            claim_candidates = deduplicate_candidates(claim_candidates)
            # Preserve contradiction candidates before filling the remaining
            # cap with ordinary discovery results.
            contradiction = [
                item
                for item in claim_candidates
                if item.purpose == "contradiction"
            ]
            discovery = [
                item
                for item in claim_candidates
                if item.purpose == "discovery"
            ]
            retained = (
                contradiction[:2] + discovery
            )[: cfg.max_candidates_per_claim]
            candidates.extend(retained)
            report_lines.append(
                f"[{claim.id}] retained {len(retained)} candidate records; "
                "zero verified citations"
            )
            if timed_out:
                break

        if timed_out:
            report_lines.append(
                f"Discovery time budget ({cfg.max_duration_seconds:.0f}s) "
                f"exceeded after {claims_processed} of {len(targets)} "
                "claims — handing off with the candidates found so far."
            )

        candidates = deduplicate_candidates(candidates)
        display_sources = [
            {
                "candidate_id": item.candidate_id,
                "claim_id": item.claim_id,
                "claim_text": graph.claims[item.claim_id].text,
                "identifier": item.doi or item.paper_id or item.canonical_url,
                "citation": item.title,
                "authority": item.authority,
                "status": "candidate_only",
            }
            for item in candidates
        ]
        return {
            "candidates_found": len(candidates),
            "claims_searched": claims_processed,
            "candidates": [
                item.model_dump(mode="json") for item in candidates
            ],
            "search_audit": [
                item.model_dump(mode="json") for item in audit
            ],
            "report": (
                "Candidate discovery completed. Search results were not linked "
                "to claims and did not change verification status.\n"
                + "\n".join(report_lines)
            ),
            "timed_out": timed_out,
            "sources_added": 0,
            "claims_linked": 0,
            "sources": display_sources,
        }

    async def _search_plan(
        self,
        llm: Any,
        claim: Claim,
        model: str | None,
    ) -> ClaimSearchPlan:
        try:
            result = await llm.complete_structured(
                model=model,
                system=(
                    "Create a neutral scholarly search plan for one atomic "
                    "proposal claim. Do not answer the claim and do not invent "
                    "sources. Provide three complementary discovery queries and "
                    "one query designed to find negative, null, conflicting, or "
                    "boundary-condition evidence. Queries must be concise.\n\n"
                    "Each query is sent verbatim to multiple academic search "
                    "APIs (OpenAIRE, OpenAlex, Semantic Scholar, arXiv, and "
                    "others) fanned out in parallel. Several of them do not "
                    "support boolean/advanced query syntax — write each query "
                    "as plain natural-language keywords only: no quoted "
                    "phrases, no AND/OR, no parentheses. Prefer 3-6 bare "
                    "keywords over a boolean expression (e.g. "
                    "'agricultural residue secondary biomass regional "
                    "bioeconomy', not \"('agricultural residues' OR 'crop "
                    "residues') AND ('secondary biomass' OR ...)\")."
                ),
                user=f"CLAIM:\n{claim.text}",
                response_model=ClaimSearchPlan,
                temperature=0.0,
                max_tokens=700,
            )
            discovery = [
                _sanitize_query(item)[:180]
                for item in result.discovery_queries
                if item.strip()
            ]
            contradiction = [
                _sanitize_query(item)[:180]
                for item in result.contradiction_queries
                if item.strip()
            ]
            if discovery:
                return ClaimSearchPlan(
                    discovery_queries=discovery[:4],
                    contradiction_queries=contradiction[:2],
                )
        except Exception:
            # A deterministic plan keeps discovery usable with local/test
            # gateways that do not implement structured completion.
            pass

        base = _sanitize_query(claim.text)[:150]
        return ClaimSearchPlan(
            discovery_queries=[
                base,
                f"{base} systematic review",
                f"{base} methods evidence",
            ],
            contradiction_queries=[
                f"{base} limitations conflicting evidence",
            ],
        )


@NodeRegistry.register
class ScholarlyCandidateDiscoveryAgent(_ScholarlyCandidateDiscovery):
    type_name = "ScholarlyCandidateDiscoveryAgent"
    description = (
        "Find scholarly candidate records with multi-query and contradiction "
        "searches. Candidates are never treated as verified evidence."
    )


@NodeRegistry.register
class EvidenceAgent(_ScholarlyCandidateDiscovery):
    """Compatibility alias for saved workflows.

    Its semantics are intentionally corrected: it now discovers candidates
    only. New workflows should use ``ScholarlyCandidateDiscoveryAgent``.
    """

    type_name = "EvidenceAgent"
    description = (
        "Legacy alias for ScholarlyCandidateDiscoveryAgent; discovery only."
    )
