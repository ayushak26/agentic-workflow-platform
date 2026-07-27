from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.mcp.server as mcp_server
from app.proposal_graph.evidence_verification import (
    ClaimSupportVerdict,
    verify_claim_against_text,
)
from app.proposal_graph.models import EvidenceStance


class VerdictLLM:
    def __init__(self, verdict: ClaimSupportVerdict):
        self.verdict = verdict
        self.calls = []

    async def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.verdict


@pytest.mark.asyncio
async def test_verified_support_requires_quote_from_source():
    llm = VerdictLLM(
        ClaimSupportVerdict(
            stance=EvidenceStance.SUPPORTS,
            confidence=0.91,
            reason="Directly stated.",
            supporting_quote="Residue availability increased by 24 percent.",
        )
    )
    verdict = await verify_claim_against_text(
        llm,
        claim="Residue availability increased.",
        source_text="The trial found: Residue availability increased by 24 percent.",
    )
    assert verdict.stance == EvidenceStance.SUPPORTS
    assert verdict.confidence == 0.91


@pytest.mark.asyncio
async def test_invented_supporting_quote_fails_closed():
    llm = VerdictLLM(
        ClaimSupportVerdict(
            stance=EvidenceStance.SUPPORTS,
            confidence=0.99,
            reason="Looks supportive.",
            supporting_quote="This sentence does not exist.",
        )
    )
    verdict = await verify_claim_against_text(
        llm,
        claim="The method is validated.",
        source_text="The method was tested in one laboratory.",
    )
    assert verdict.stance == EvidenceStance.INSUFFICIENT
    assert verdict.confidence == 0.0


@pytest.mark.asyncio
async def test_mcp_validate_citation_uses_structured_gateway(monkeypatch):
    llm = VerdictLLM(
        ClaimSupportVerdict(
            stance=EvidenceStance.SUPPORTS,
            confidence=0.84,
            reason="Exact statement.",
            supporting_quote="The system reduced water use by 18%.",
        )
    )

    class Query:
        def fetch_objects(self, **kwargs):
            return SimpleNamespace(
                objects=[
                    SimpleNamespace(
                        properties={
                            "text": "The system reduced water use by 18%."
                        }
                    )
                ]
            )

    collection = SimpleNamespace(query=Query())
    weaviate = SimpleNamespace(
        collections=SimpleNamespace(get=lambda name: collection)
    )
    monkeypatch.setattr(
        mcp_server,
        "_services",
        lambda: {"weaviate": weaviate, "llm": llm},
    )
    result = await mcp_server.call_tool(
        "validate_citation",
        {
            "chunk_id": "chunk-1",
            "claim": "The system reduced water use.",
            "session_id": "session-1",
        },
    )
    payload = json.loads(result[0].text)

    assert payload["score"] == 0.84
    assert payload["stance"] == "supports"
    assert payload["supporting_quote"] == "The system reduced water use by 18%."
    assert len(llm.calls) == 1
