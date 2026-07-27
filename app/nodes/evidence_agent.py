"""
EvidenceAgent — MCP-driven scholarly discovery that writes EvidenceSource
objects into the ProposalGraph.

Role in the pipeline (strategy doc "Evidence Factory", discovery half)
----------------------------------------------------------------------
Section drafters currently make state-of-the-art / impact claims with no
citations — a scoring risk. This node takes claims (or claim-like topics),
searches open scholarly sources via the paper-search-mcp server, and records
each candidate paper as a typed EvidenceSource in the graph. It links sources
to the originating Claim so a later verification pass (PaperQA2) can flip
Claim.verification from MISSING -> verified.

Provenance discipline (why this node is trustworthy)
----------------------------------------------------
The LLM is used ONLY to turn a claim into good search queries. The
machine-verifiable facts (DOI, title, source) are taken DIRECTLY from the MCP
tool result, never from LLM output — an LLM can hallucinate a DOI, the tool
cannot. This mirrors the MCPAgent's session_id anti-spoofing rule: trusted
facts come from the tool boundary, not the model.

Scope
-----
- Second MCP server, reached through the EXISTING MCPClient (config-driven
  server list). No new client plumbing.
- Discovery only. It fills EvidenceSource and links Claim.evidence_source_ids.
  It does NOT assert that a paper supports a claim — that is verification,
  deferred to PaperQA2 per the backlog. EvidenceSource.authority is recorded;
  Claim.verification stays MISSING until a verifier runs.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import Authority, Claim, EvidenceSource, Status


class EvidenceAgentOutput(BaseModel):
    """Validated output for EvidenceAgent.run() (output_schema ClassVar)."""
    sources_added: int = 0
    claims_linked: int = 0
    report: str = ""
    # Per-paper detail so a display node can render titles/DOIs per claim,
    # not just summary counts. Each item: {claim_id, claim_text, identifier,
    # citation, authority}.
    sources: list = Field(default_factory=list)


class EvidenceAgentConfig(BaseModel):
    """Node config — base.__init__ does config_schema(**raw_config)."""
    mcp_server: str = "paper-search-mcp"
    tool: str = "search_papers"
    sources: list | None = None
    max_per_claim: int = 5
    claim_types: list | None = None
    model: str | None = None

# EU-lawful, open-first sources aligned with the call's posture. All are
# keyless-runnable and marked reliable (✅) in paper-search-mcp's capability
# matrix. CORE benefits from a free key but runs without one. Google Scholar /
# SSRN / CiteSeerX / BASE deliberately excluded (⚠️ unstable or bot-gated).
_DEFAULT_SOURCES = ["arxiv", "openalex", "europepmc", "core", "openaire",
                    "zenodo", "hal", "doaj", "pmc"]

# Map a source name to a provenance authority level.
_AUTHORITY_BY_SOURCE = {
    "arxiv": Authority.PREPRINT,
    "biorxiv": Authority.PREPRINT,
    "medrxiv": Authority.PREPRINT,
    "ssrn": Authority.PREPRINT,
    "europepmc": Authority.PEER_REVIEWED,
    "pmc": Authority.PEER_REVIEWED,
    "pubmed": Authority.PEER_REVIEWED,
    "crossref": Authority.PEER_REVIEWED,
    "openalex": Authority.PEER_REVIEWED,
    "doaj": Authority.PEER_REVIEWED,     # Directory of Open Access Journals — peer-reviewed
    "core": Authority.GREY,
    "openaire": Authority.OFFICIAL_EU,   # EU research-output graph
    "zenodo": Authority.GREY,
    "hal": Authority.GREY,
}


def _first(d: dict, *keys: str) -> Any:
    """Defensively pull the first present, non-empty field among name variants.
    paper-search-mcp's Paper dict field names may vary across versions/sources,
    so we try several rather than guessing one and silently producing wrong data."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _paper_to_source(paper: dict, source_hint: str | None) -> EvidenceSource:
    """Build an EvidenceSource from a real paper-search-mcp Paper dict.

    Verified schema (arXiv, 2026) — keys: paper_id, title, authors (a single
    ';'-separated STRING, not a list), abstract, doi (often "" for preprints),
    published_date (ISO datetime), pdf_url, url, source, categories, keywords,
    citations. Other sources (OpenAlex/Crossref) generally populate `doi`.

    Identifier precedence: real DOI -> arXiv id (paper_id when source=arxiv) ->
    canonical url. Facts come from the tool dict only, never from the model."""
    src = str(_first(paper, "source", "platform") or source_hint or "").lower()
    doi = _first(paper, "doi", "DOI")                 # "" for arXiv → treated as absent by _first
    paper_id = _first(paper, "paper_id", "id")
    url = _first(paper, "url", "pdf_url", "openaccess_url", "link")

    identifier = None
    if doi:
        d = str(doi)
        identifier = d if d.startswith(("doi:", "http")) else f"doi:{d}"
    elif paper_id and src == "arxiv":
        identifier = f"arXiv:{paper_id}"
    elif paper_id:
        identifier = f"{src or 'id'}:{paper_id}"
    elif url:
        identifier = str(url)

    title = _first(paper, "title") or "(title unavailable)"

    # authors: real schema is a ';'-separated string; also tolerate a list.
    authors_raw = _first(paper, "authors", "author")
    author_str = ""
    if isinstance(authors_raw, str) and authors_raw:
        first = authors_raw.split(";")[0].strip()
        author_str = first + (" et al." if ";" in authors_raw else "")
    elif isinstance(authors_raw, list) and authors_raw:
        author_str = str(authors_raw[0]) + (" et al." if len(authors_raw) > 1 else "")

    # year from published_date (ISO) or a plain year field
    pub = _first(paper, "published_date", "year", "published", "publication_date")
    year = ""
    if pub:
        s = str(pub)
        year = s[:4] if len(s) >= 4 and s[:4].isdigit() else s

    citation = ", ".join(p for p in [author_str, f"“{title}”", year] if p)

    # stable id: prefer identifier, else hash the title, so re-runs dedup.
    basis = identifier or title
    sid = "SRC-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]

    return EvidenceSource(
        id=sid,
        citation=citation,
        identifier=identifier,
        authority=_AUTHORITY_BY_SOURCE.get(src, Authority.UNVERIFIED),
        retrieved_at=date.today().isoformat(),
    )


@NodeRegistry.register
class EvidenceAgent(NodeType):
    """For each target claim/topic: LLM drafts queries -> paper-search-mcp
    returns papers -> node writes EvidenceSource objects into proposal_graph
    and links them to the Claim. Discovery only; no support-assertion."""

    type_name = "EvidenceAgent"

    config_schema = EvidenceAgentConfig
    output_schema = EvidenceAgentOutput

    async def run(self, state: dict, config: dict) -> dict:
        raw = state.get("proposal_graph")
        graph = raw if isinstance(raw, ProposalGraph) else ProposalGraph(**(raw or {}))

        server = config.get("mcp_server", "paper-search-mcp")
        tool = config.get("tool", "search_papers")
        sources = config.get("sources", _DEFAULT_SOURCES)
        cap = int(config.get("max_per_claim", 5))
        want_types = set(config.get("claim_types", ["state_of_art", "impact", "problem", "method"]))

        # Which claims to enrich: those of the wanted types lacking evidence.
        targets = [c for c in graph.claims.values()
                   if c.claim_type in want_types and not c.evidence_source_ids]

        # Services wired into the node (llm gateway + mcp client) — same access
        # pattern the MCPAgent uses. self.services is provided by NodeType.__init__.
        llm = self.services.get("llm")
        mcp = self.services.get("mcp_client")
        if llm is None or mcp is None:
            missing = [n for n, v in (("llm", llm), ("mcp_client", mcp)) if v is None]
            raise RuntimeError(
                f"EvidenceAgent requires services {missing}; run with the app's "
                "services dict (llm gateway + mcp_client)."
            )

        new_sources: dict[str, EvidenceSource] = {}
        updated_claims: dict[str, Claim] = {}
        found_sources: list[dict] = []   # display-friendly per-paper records
        lines: list[str] = []

        for claim in targets:
            # 1. LLM formulates a search query from the claim (query text only).
            q = await self._make_query(llm, claim, config.get("model"))

            # 2. Call the paper-search MCP tool through the existing client.
            #    Real client signature: call_tool(name, arguments, server=...),
            #    returning the tool's first TextContent as a STRING (already
            #    unwrapped). Facts come back from the tool, not the model.
            try:
                # Real search_papers signature (verified against server.py):
                #   search_papers(query: str, max_results_per_source: int = 5,
                #                 sources: str = "all", year: str | None = None)
                # sources is a COMMA-SEPARATED STRING (the tool _parse_sources()
                # splits on commas), NOT a list. Passing a list yields zero valid
                # sources and an empty result — the cause of earlier "no results".
                sources_arg = (
                    ",".join(sources) if isinstance(sources, (list, tuple)) else str(sources)
                )
                raw = await mcp.call_tool(
                    name=tool,
                    arguments={
                        "query": q,
                        "sources": sources_arg,
                        "max_results_per_source": cap,
                    },
                    server=server,
                )
            except Exception as exc:  # tool/transport failure — record, continue
                lines.append(f"[{claim.id}] search failed: {exc}")
                continue

            papers = self._extract_papers(raw)
            linked_ids: list[str] = []
            for paper in papers[:cap]:
                src = _paper_to_source(paper, None)
                new_sources[src.id] = src
                linked_ids.append(src.id)
                found_sources.append({
                    "claim_id": claim.id,
                    "claim_text": claim.text,
                    "identifier": src.identifier,
                    "citation": src.citation,
                    "authority": src.authority.value,
                })

            if linked_ids:
                updated = claim.model_copy(update={
                    "evidence_source_ids": claim.evidence_source_ids + linked_ids,
                    # discovery only: verification stays MISSING until a verifier runs
                    "verification": Status.PARTIAL,
                })
                updated_claims[claim.id] = updated
                lines.append(f"[{claim.id}] +{len(linked_ids)} sources (query: {q[:60]})")
            else:
                lines.append(f"[{claim.id}] no results")

        report = (f"EvidenceAgent: enriched {len(updated_claims)}/{len(targets)} "
                  f"claims, added {len(new_sources)} sources\n" + "\n".join(lines))

        # Write back via the proposal_graph reducer (merge is field-wise safe).
        graph_delta = ProposalGraph(
            evidence_sources=new_sources,
            claims=updated_claims,
        )
        return {
            "sources_added": len(new_sources),
            "claims_linked": len(updated_claims),
            "report": report,
            "sources": found_sources,
            "__state__": {"proposal_graph": graph_delta},
        }

    async def _make_query(self, llm, claim: Claim, model: str | None) -> str:
        """LLM turns a claim into a focused scholarly search query. Output is a
        query STRING only — never used to assert facts."""
        prompt = (
            "Turn this proposal claim into ONE focused scholarly search query "
            "(6-12 words, key technical terms, no punctuation, no boolean "
            "operators). Return only the query.\n\nCLAIM: " + claim.text
        )
        resp = await llm.complete(
            system=("You convert research claims into concise scholarly search "
                    "queries. Return only the query text, nothing else."),
            user=prompt,
            model=model,
        )
        return (getattr(resp, "text", None) or str(resp)).strip().splitlines()[0][:120]

    @staticmethod
    def _extract_papers(raw: Any) -> list[dict]:
        """Normalise the tool's return into a list of paper dicts.

        Your MCPClient.call_tool already unwraps to the first TextContent's
        text, so `raw` is normally a JSON STRING. We also tolerate an already-
        parsed dict/list in case a caller hands us structured data. We do NOT
        need to dig through {content:[{text:...}]} envelopes — the client did
        that. This keeps the trust boundary clean: we parse tool JSON, we never
        read LLM output here."""
        import json

        # If the client handed back a JSON string, parse it.
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                return []
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return []

        # paper-search-mcp search_papers commonly returns {"papers":[...]} or a
        # bare list; tolerate a few shapes without assuming one.
        if isinstance(raw, dict):
            for key in ("papers", "results", "data"):
                val = raw.get(key)
                if isinstance(val, list):
                    return [p for p in val if isinstance(p, dict)]
            # single paper dict
            if "title" in raw or "doi" in raw:
                return [raw]
            return []
        if isinstance(raw, list):
            return [p for p in raw if isinstance(p, dict)]
        return []