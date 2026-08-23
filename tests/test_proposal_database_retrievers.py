from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import app.nodes  # noqa: F401
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import Claim
from app.proposal_graph.state import proposal_graph_state_update
from app.tools.database_lookup import DatabaseResponse
from app.tools.web_io import WebResult, WebSearchResponse
from app.nodes.registry import NodeRegistry
from app.runtime.state import WorkflowState


class MemoryObjectStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str | None = None,
    ) -> None:
        del content_type
        self.blobs[key] = data


class StubDatabaseLookup:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def query_eurostat(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return DatabaseResponse(
            endpoint=(
                "https://ec.europa.eu/eurostat/api/dissemination/"
                f"statistics/1.0/data/{query.dataset_code}"
            ),
            parameters={"unit": "CLV10_MEUR", "geo": ["DE", "FR"]},
            raw=json.dumps(self.payload).encode("utf-8"),
            content_type="application/json",
            headers={"etag": '"test-version"'},
        )


class StubWebSearch:
    async def search(self, query, **kwargs):
        del kwargs
        return WebSearchResponse(
            query=query,
            requested_provider="auto",
            actual_provider="stub",
            results=[
                WebResult(
                    title="Official CORDIS project",
                    url="https://cordis.europa.eu/project/id/101000001",
                    snippet="Official candidate project record.",
                    score=0.9,
                ),
                WebResult(
                    title="Copied project summary",
                    url="https://example.com/copied-project",
                    snippet="An unofficial copy that must be rejected.",
                    score=0.8,
                ),
            ],
        )


class InternalExtractionLLM:
    async def complete_structured(self, **kwargs):
        model = kwargs["response_model"]
        return model(
            facts=[
                {
                    "question": "Which facilities does partner Alpha provide?",
                    "fact_key": "partner_alpha.facility",
                    "fact_value": "Pilot fermentation facility",
                    "linked_claim_ids": ["CL-1"],
                    "source_name": "partner_alpha.md",
                    "exact_passage": (
                        "Partner Alpha operates a pilot fermentation facility."
                    ),
                    "locator": "Facilities section",
                },
                {
                    "question": "Which staff are committed?",
                    "fact_key": "partner_alpha.staff",
                    "fact_value": "Ten researchers",
                    "source_name": "partner_alpha.md",
                    "exact_passage": "Ten researchers are committed.",
                },
            ],
            unresolved_questions=[],
        )


def _proposal_state() -> WorkflowState:
    graph = ProposalGraph(
        claims={
            "CL-1": Claim(
                id="CL-1",
                text="The proposed pilot has sufficient facilities.",
            )
        }
    )
    return cast(WorkflowState, proposal_graph_state_update(graph))


async def test_structured_dataset_retriever_creates_hashed_traceable_rows():
    payload = {
        "version": "2.0",
        "class": "dataset",
        "label": "Test GDP dataset",
        "id": ["unit", "geo", "time"],
        "size": [1, 2, 2],
        "dimension": {
            "unit": {"category": {"index": {"CLV10_MEUR": 0}}},
            "geo": {"category": {"index": {"DE": 0, "FR": 1}}},
            "time": {"category": {"index": {"2023": 0, "2024": 1}}},
        },
        "value": [100.0, 101.0, 200.0, 201.0],
    }
    store = MemoryObjectStore()
    database = StubDatabaseLookup(payload)
    node = NodeRegistry.get("StructuredDatasetRetrieverAgent")(
        "structured",
        {
            "queries": [
                {
                    "query_id": "DATA-Q-1",
                    "claim_id": "CL-1",
                    "dataset_code": "nama_10_gdp",
                    "target": "GDP baseline",
                    "filters": {
                        "unit": ["CLV10_MEUR"],
                        "geo": ["DE", "FR"],
                    },
                    "start_period": "2023",
                    "end_period": "2024",
                }
            ],
            "auto_plan_queries": False,
        },
        services={"database_lookup": database, "object_store": store},
    )

    result = await node.run(_proposal_state(), node.config.model_dump())

    assert result["records_retrieved"] == 4
    assert result["queries_completed"] == 1
    assert result["audit"][0]["count_reconciliation"]["complete"] is True
    assert result["audit"][0]["count_reconciliation"]["truncated"] is False
    assert len({item["row_sha256"] for item in result["records"]}) == 4
    assert all(
        item["verification_status"] == "verified_structured_record"
        and item["drafting_allowed"] is False
        for item in result["records"]
    )
    assert len(store.blobs) == 1


async def test_prior_project_retriever_keeps_only_official_candidates():
    node = NodeRegistry.get("PriorProjectRetrieverAgent")(
        "prior_projects",
        {
            "research_briefs": [
                {
                    "brief_id": "RQ-PRIOR-1",
                    "track": "prior_projects_and_synergies",
                    "question": "Which EU projects addressed farm residues?",
                    "purpose": "prior_art",
                    "linked_claim_ids": ["CL-1"],
                    "max_tool_calls": 2,
                }
            ],
            "sources": ["cordis"],
        },
        services={"web_search": StubWebSearch()},
    )

    result = await node.run({}, node.config.model_dump())

    assert result["verification_status"] == "candidate_only"
    assert result["projects_found"] == 1
    assert result["candidates"][0]["canonical_url"].startswith(
        "https://cordis.europa.eu/"
    )
    assert result["candidates"][0]["metadata_status"] == "candidate"


async def test_internal_retriever_requires_exact_passage_and_human_approval():
    node = NodeRegistry.get("InternalProjectEvidenceRetrieverAgent")(
        "internal",
        {
            "source_registry": {
                "source_registry": [
                    {
                        "source_id": "ISRC-1",
                        "file_name": "partner_alpha.md",
                        "source_class": "consortium_or_partner_supplied_fact",
                        "approval_status": "pending",
                    }
                ]
            },
            "source_text": (
                "--- partner_alpha.md ---\n"
                "Partner Alpha operates a pilot fermentation facility.\n"
                "No staffing commitment is stated."
            ),
            "query_internal_index": False,
        },
        services={"llm": InternalExtractionLLM()},
    )

    result = await node.run(_proposal_state(), node.config.model_dump())

    assert len(result["records"]) == 1
    assert len(result["pending_human_approval"]) == 1
    assert result["records"][0]["verification_status"] == (
        "exact_passage_matched_pending_human_approval"
    )
    assert result["records"][0]["drafting_allowed"] is False
    assert result["rejected_facts"] == [
        {
            "fact_key": "partner_alpha.staff",
            "source_name": "partner_alpha.md",
            "reason": "exact passage was not present in source",
        }
    ]


async def test_truth_graph_fails_closed_without_evidence_gate_approval():
    verified_claim = {
        "claim_id": "CL-1",
        "original_text": "The proposed pilot has sufficient facilities.",
        "atomic_claim": "The proposed pilot has sufficient facilities.",
        "claim_type": "method",
        "materiality": "important",
        "evidence_requirement": "An exact supporting passage.",
        "final_status": "verified",
        "verified_sentence": "The proposed pilot has sufficient facilities.",
    }
    node_class = NodeRegistry.get("ProposalTruthGraphAgent")
    pending = node_class(
        "truth_pending",
        {"verified_claims": [verified_claim]},
    )

    pending_result = await pending.run(
        _proposal_state(),
        pending.config.model_dump(),
    )

    assert pending_result["drafting_allowed"] is False
    assert pending_result["approval_required"] is True

    approved = node_class(
        "truth_approved",
        {
            "verified_claims": [verified_claim],
            "evidence_approval_decision": "approve",
        },
    )
    approved_result = await approved.run(
        _proposal_state(),
        approved.config.model_dump(),
    )

    assert approved_result["drafting_allowed"] is True
    assert approved_result["approval_required"] is False
