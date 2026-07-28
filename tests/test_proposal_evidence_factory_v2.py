from __future__ import annotations

import json
from pathlib import Path

from app.evidence.models import CandidateSource, FullTextDocument
from app.evidence.retrieval import candidate_from_paper, utc_now
from app.nodes.evidence_agent import ScholarlyCandidateDiscoveryAgent
from app.nodes.full_text_evidence_acquirer import FullTextEvidenceAcquirer
from app.nodes.proposal_evidence_factory import (
    PairVerdict,
    ProposalEvidenceFactoryAgent,
)
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import Claim, Status
from app.proposal_graph.state import proposal_graph_state_update


class StubObjectStore:
    def __init__(self, blobs=None):
        self.blobs = blobs or {}
        self.puts = []

    def get_bytes(self, key):
        return self.blobs[key]

    def put_bytes(self, data, key, content_type=None):
        self.blobs[key] = data
        self.puts.append((key, content_type))


class DiscoveryLLM:
    async def complete_structured(self, **kwargs):
        response_model = kwargs["response_model"]
        return response_model(
            discovery_queries=["agricultural residue availability remote sensing"],
            contradiction_queries=[
                "agricultural residue remote sensing limitations"
            ],
        )


class DiscoveryMCP:
    async def call_tool(self, *, name, arguments, server):
        purpose = (
            "limitation" if "limitations" in arguments["query"] else "support"
        )
        return json.dumps(
            {
                "papers": [
                    {
                        "paper_id": purpose,
                        "title": f"Paper about {purpose}",
                        "authors": "A. Author; B. Author",
                        "published_date": "2025-02-01",
                        "doi": f"10.1000/{purpose}",
                        "source": "openalex",
                        "abstract": "Candidate metadata only.",
                        "is_retracted": False,
                    }
                ]
            }
        )


async def test_candidate_discovery_never_changes_claim_verification():
    graph = ProposalGraph(
        claims={
            "CL-1": Claim(
                id="CL-1",
                text="Remote sensing improves agricultural residue estimates.",
                claim_type="state_of_art",
            )
        }
    )
    node = ScholarlyCandidateDiscoveryAgent(
        "discover",
        {
            "sources": ["openalex"],
            "max_results_per_source": 1,
            "max_candidates_per_claim": 4,
        },
        services={
            "llm": DiscoveryLLM(),
            "mcp_client": DiscoveryMCP(),
        },
    )
    result = await node.run(
        proposal_graph_state_update(graph),
        node.config.model_dump(),
    )

    assert result["candidates_found"] == 2
    assert result["sources_added"] == 0
    assert result["claims_linked"] == 0
    assert "__state__" not in result
    assert graph.claims["CL-1"].verification == Status.MISSING
    assert graph.claims["CL-1"].evidence_source_ids == []
    assert {item["purpose"] for item in result["candidates"]} == {
        "discovery",
        "contradiction",
    }


async def test_full_text_acquirer_stores_immutable_pdf_and_pages():
    from weasyprint import HTML

    candidate = _candidate(
        "paper-download",
        "10.1000/download",
        "Downloadable source",
    )
    pdf = HTML(
        string="<h1>Results</h1><p>Residue mapping accuracy improved.</p>"
    ).write_pdf()

    class DownloadMCP:
        async def call_tool(self, *, name, arguments, server):
            output = Path(arguments["save_path"]) / "source.pdf"
            output.write_bytes(pdf)
            assert arguments["use_scihub"] is False
            return str(output)

    store = StubObjectStore()
    node = FullTextEvidenceAcquirer(
        "acquire",
        {
            "candidates": [candidate.model_dump(mode="json")],
            "fail_when_none_acquired": True,
        },
        services={
            "mcp_client": DownloadMCP(),
            "object_store": store,
        },
    )
    result = await node.run(
        {"inputs": {"SYSTEM.run_id": "run-acquire"}},
        node.config.model_dump(),
    )

    assert result["full_text_documents_acquired"] == 1
    document = result["documents"][0]
    assert document["page_count"] == 1
    assert document["version_id"].startswith("VER-")
    assert store.blobs[document["pdf_object_key"]].startswith(b"%PDF-")
    pages_payload = json.loads(
        store.blobs[document["pages_object_key"]].decode()
    )
    assert pages_payload["pages"][0]["page_num"] == 1
    assert "Residue mapping accuracy" in pages_payload["pages"][0]["text"]


class VerificationLLM:
    async def complete_structured(self, **kwargs):
        payload = kwargs["user"].split(
            "RETRIEVED PASSAGES (JSON):\n",
            1,
        )[1]
        passages = json.loads(payload)
        return PairVerdict(
            stance="supports_directly",
            confidence=0.94,
            exact_quote=passages[0]["text"],
            reason="The passage directly states the claim.",
        )


def _candidate(candidate_id: str, doi: str, title: str) -> CandidateSource:
    return candidate_from_paper(
        {
            "paper_id": candidate_id,
            "title": title,
            "authors": "A. Researcher; B. Researcher",
            "published_date": "2025-01-01",
            "doi": doi,
            "source": "openalex",
            "is_retracted": False,
        },
        claim_id="CL-1",
        query="residue mapping accuracy",
        purpose="discovery",
    )


def _document(
    candidate: CandidateSource,
    *,
    document_id: str,
    pages_key: str,
    digest: str,
) -> FullTextDocument:
    return FullTextDocument(
        document_id=document_id,
        candidate_id=candidate.candidate_id,
        claim_id="CL-1",
        title=candidate.title,
        citation=f"{candidate.title}.",
        identifier=f"doi:{candidate.doi}",
        canonical_url=f"https://doi.org/{candidate.doi}",
        source_type="openalex",
        authority="peer_reviewed",
        independence_group=candidate.independence_group,
        version_id=f"VER-{digest[:8]}",
        content_sha256=digest,
        pdf_object_key=f"{pages_key}.pdf",
        pages_object_key=pages_key,
        page_count=1,
        canonical_metadata_validated=True,
        retraction_status="clear",
        fetched_at=utc_now(),
    )


async def test_factory_releases_only_full_text_page_level_citations():
    claim_text = (
        "Satellite and field data fusion improves residue availability estimates."
    )
    graph = ProposalGraph(
        claims={
            "CL-1": Claim(
                id="CL-1",
                text=claim_text,
                claim_type="state_of_art",
                proposal_section="1.2",
            )
        }
    )
    first = _candidate("paper-1", "10.1000/paper1", "First source")
    second = _candidate("paper-2", "10.1000/paper2", "Second source")
    documents = [
        _document(
            first,
            document_id="DOC-1",
            pages_key="evidence/doc-1.pages.json",
            digest="a" * 64,
        ),
        _document(
            second,
            document_id="DOC-2",
            pages_key="evidence/doc-2.pages.json",
            digest="b" * 64,
        ),
    ]
    store = StubObjectStore(
        {
            "evidence/doc-1.pages.json": json.dumps(
                {
                    "pages": [
                        {
                            "page_num": 4,
                            "text": claim_text,
                        }
                    ]
                }
            ).encode(),
            "evidence/doc-2.pages.json": json.dumps(
                {
                    "pages": [
                        {
                            "page_num": 7,
                            "text": claim_text,
                        }
                    ]
                }
            ).encode(),
        }
    )
    node = ProposalEvidenceFactoryAgent(
        "verify",
        {
            "candidates": [
                first.model_dump(mode="json"),
                second.model_dump(mode="json"),
            ],
            "documents": [
                item.model_dump(mode="json") for item in documents
            ],
            "search_audit": [
                {
                    "claim_id": "CL-1",
                    "query": "residue mapping limitations",
                    "source_or_database": "openalex",
                    "searched_at": utc_now(),
                    "result_count": 0,
                    "purpose": "contradiction",
                }
            ],
        },
        services={
            "llm": VerificationLLM(),
            "object_store": store,
        },
    )
    result = await node.run(
        proposal_graph_state_update(graph),
        node.config.model_dump(),
    )

    assert result["verified_claims"][0]["final_status"] == "verified"
    assert len(result["citation_registry"]) == 2
    assert len(result["claim_evidence_links"]) == 2
    assert {item["locator"]["page"] for item in result["claim_evidence_links"]} == {
        4,
        7,
    }
    assert result["blocking_issues"] == []
    assert result["qa_report"]["exact_locator_rate"] == 1.0
    assert result["graph_sources_added"] == 2
    assert result["graph_relations_added"] == 2


async def test_factory_fails_closed_when_quote_is_not_in_source():
    graph = ProposalGraph(
        claims={
            "CL-1": Claim(
                id="CL-1",
                text="The approach improves biodiversity.",
                claim_type="impact",
            )
        }
    )
    candidate = _candidate("paper-1", "10.1000/paper1", "Source")
    document = _document(
        candidate,
        document_id="DOC-1",
        pages_key="evidence/doc.pages.json",
        digest="c" * 64,
    )
    store = StubObjectStore(
        {
            "evidence/doc.pages.json": json.dumps(
                {
                    "pages": [
                        {
                            "page_num": 3,
                            "text": "The study measured soil moisture only.",
                        }
                    ]
                }
            ).encode()
        }
    )

    class InventedQuoteLLM:
        async def complete_structured(self, **kwargs):
            return PairVerdict(
                stance="supports_directly",
                confidence=0.99,
                exact_quote="This quote is invented.",
                reason="Unsupported.",
            )

    node = ProposalEvidenceFactoryAgent(
        "verify",
        {
            "candidates": [candidate.model_dump(mode="json")],
            "documents": [document.model_dump(mode="json")],
            "search_audit": [],
        },
        services={
            "llm": InventedQuoteLLM(),
            "object_store": store,
        },
    )
    result = await node.run(
        proposal_graph_state_update(graph),
        node.config.model_dump(),
    )
    assert result["verified_claims"][0]["final_status"] == "insufficient"
    assert result["citation_registry"] == []
    assert result["graph_sources_added"] == 0
    assert result["blocking_issues"]
