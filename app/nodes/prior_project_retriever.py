"""Official prior-project discovery for Horizon proposal positioning.

CORDIS, LIFE and EIP-AGRI results are useful for prior-art comparison,
synergies and non-duplication. Search hits remain candidate context: they must
pass ``ResearchSourceAcquirer`` and exact-passage verification before they can
support a proposal claim.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from app.evidence.models import (
    CandidateSource,
    SearchAuditRecord,
)
from app.evidence.retrieval import (
    deduplicate_candidates,
    stable_id,
    utc_now,
)
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.research.deep_research import ResearchBrief


PriorProjectSource = Literal["cordis", "life", "eip_agri"]


_SOURCE_RULES: dict[str, dict[str, Any]] = {
    "cordis": {
        "query_prefix": "site:cordis.europa.eu/project/id",
        "hosts": ("cordis.europa.eu",),
    },
    "life": {
        "query_prefix": (
            "site:cinea.ec.europa.eu/programmes/life OR "
            "site:environment.ec.europa.eu/topics/life-environment"
        ),
        "hosts": ("cinea.ec.europa.eu", "environment.ec.europa.eu"),
    },
    "eip_agri": {
        "query_prefix": "site:eu-cap-network.ec.europa.eu/eip-agri",
        "hosts": ("eu-cap-network.ec.europa.eu",),
    },
}


class PriorProjectRetrieverInput(BaseModel):
    pass


class PriorProjectRetrieverConfig(BaseModel):
    research_briefs: str | list[ResearchBrief]
    sources: list[PriorProjectSource] = Field(
        default_factory=lambda: ["cordis", "life", "eip_agri"]
    )
    provider: Literal["auto", "tavily", "openai", "kimi"] = "auto"
    max_briefs: int = Field(default=5, ge=1, le=20)
    max_results_per_source: int = Field(default=5, ge=1, le=20)
    max_candidates_per_claim: int = Field(default=8, ge=1, le=30)
    max_total_searches: int = Field(default=15, ge=1, le=50)
    max_parallel_searches: int = Field(default=3, ge=1, le=5)
    only_prior_project_track: bool = True

    @field_validator("research_briefs", mode="before")
    @classmethod
    def _coerce_briefs(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if "{{" in text and "}}" in text:
                return value
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "research_briefs must be a template or JSON array"
                ) from exc
        if isinstance(value, list):
            return TypeAdapter(list[ResearchBrief]).validate_python(value)
        raise ValueError("research_briefs must be a template or list")


class PriorProjectRetrieverOutput(BaseModel):
    candidates: list[CandidateSource] = Field(default_factory=list)
    search_audit: list[SearchAuditRecord] = Field(default_factory=list)
    projects_found: int = 0
    searches_completed: int = 0
    searches_failed: int = 0
    verification_status: Literal["candidate_only"] = "candidate_only"
    note: str = (
        "Prior-project search results are candidate context, not verified "
        "evidence. Acquire the official record and verify an exact passage "
        "before citing a project outcome, performance result, or conclusion."
    )
    report: str = ""


@NodeRegistry.register
class PriorProjectRetrieverAgent(NodeType):
    type_name = "PriorProjectRetrieverAgent"
    description = (
        "Search official CORDIS, LIFE and EIP-AGRI project records for "
        "precedents and synergies. Outputs candidates only; exact-passage "
        "verification remains mandatory."
    )
    input_schema = PriorProjectRetrieverInput
    config_schema = PriorProjectRetrieverConfig
    output_schema = PriorProjectRetrieverOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        del state
        cfg = PriorProjectRetrieverConfig(**resolved_config)
        if isinstance(cfg.research_briefs, str):
            raise ValueError("research_briefs template did not resolve")
        service = self.services.get("web_search")
        if service is None:
            raise RuntimeError(
                "PriorProjectRetrieverAgent requires web_search service"
            )
        briefs = [
            item
            for item in cfg.research_briefs
            if (
                not cfg.only_prior_project_track
                or item.track == "prior_projects_and_synergies"
            )
        ][: cfg.max_briefs]
        jobs = [
            (brief, source)
            for brief in briefs
            for source in cfg.sources
        ][: cfg.max_total_searches]
        semaphore = asyncio.Semaphore(cfg.max_parallel_searches)
        candidates: list[CandidateSource] = []
        audits: list[SearchAuditRecord] = []
        failures = 0

        async def _search_one(
            brief: ResearchBrief,
            source: str,
        ) -> None:
            nonlocal failures
            rule = _SOURCE_RULES[source]
            query = f"{rule['query_prefix']} {brief.question}".strip()
            searched_at = utc_now()
            try:
                async with semaphore:
                    response = await service.search(
                        query,
                        provider=cfg.provider,
                        top_k=cfg.max_results_per_source,
                        fallback_to_openai=True,
                    )
                raw_results = [
                    asdict(item)
                    if is_dataclass(item)
                    else dict(item)
                    if isinstance(item, dict)
                    else {
                        "title": getattr(item, "title", ""),
                        "url": getattr(item, "url", ""),
                        "snippet": getattr(item, "snippet", ""),
                    }
                    for item in response.results
                ]
                retained = [
                    item
                    for item in raw_results
                    if _allowed_official_url(
                        str(item.get("url") or ""),
                        rule["hosts"],
                    )
                ]
                audits.extend(
                    SearchAuditRecord(
                        claim_id=claim_id,
                        query=query,
                        source_or_database=source,
                        filters={
                            "official_hosts": list(rule["hosts"]),
                            "max_results": cfg.max_results_per_source,
                        },
                        searched_at=searched_at,
                        result_count=len(retained),
                        purpose="discovery",
                    )
                    for claim_id in brief.linked_claim_ids
                )
                for claim_id in brief.linked_claim_ids:
                    for item in retained:
                        url = str(item.get("url") or "").strip()
                        title = str(
                            item.get("title") or f"{source} project record"
                        ).strip()
                        candidates.append(
                            CandidateSource(
                                candidate_id=stable_id(
                                    "CAND",
                                    claim_id,
                                    source,
                                    url,
                                ),
                                claim_id=claim_id,
                                query=query,
                                purpose="discovery",
                                source=source,
                                title=title,
                                canonical_url=url,
                                abstract=str(item.get("snippet") or "")[:2_000]
                                or None,
                                authority="official_eu",
                                independence_group=stable_id(
                                    "IG",
                                    source,
                                    url,
                                    length=12,
                                ),
                                metadata_status="candidate",
                                retraction_status="unchecked",
                                evidence_access="metadata_only",
                                retrieved_at=searched_at,
                            )
                        )
            except Exception as exc:
                failures += 1
                audits.extend(
                    SearchAuditRecord(
                        claim_id=claim_id,
                        query=query,
                        source_or_database=source,
                        filters={
                            "official_hosts": list(rule["hosts"]),
                            "max_results": cfg.max_results_per_source,
                        },
                        searched_at=searched_at,
                        result_count=0,
                        purpose="discovery",
                        error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                    for claim_id in brief.linked_claim_ids
                )

        await asyncio.gather(
            *(_search_one(brief, source) for brief, source in jobs)
        )
        candidates = deduplicate_candidates(candidates)
        by_claim: dict[str, int] = {}
        bounded: list[CandidateSource] = []
        for item in candidates:
            count = by_claim.get(item.claim_id, 0)
            if count >= cfg.max_candidates_per_claim:
                continue
            bounded.append(item)
            by_claim[item.claim_id] = count + 1
        return PriorProjectRetrieverOutput(
            candidates=bounded,
            search_audit=audits,
            projects_found=len(bounded),
            searches_completed=max(0, len(jobs) - failures),
            searches_failed=failures,
            report=(
                f"Ran {len(jobs)} bounded search(es) against official "
                f"CORDIS/LIFE/EIP-AGRI domains and retained {len(bounded)} "
                "candidate project record(s). No project result was promoted "
                "to verified evidence."
            ),
        ).model_dump(mode="json")


def _allowed_official_url(url: str, hosts: tuple[str, ...]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in hosts)
