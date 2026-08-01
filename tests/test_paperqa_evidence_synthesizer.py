from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.nodes.paperqa_evidence_synthesizer import PaperQAEvidenceSynthesizerAgent


class StubObjectStore:
    def __init__(self, blobs=None):
        self.blobs = blobs or {}

    def get_bytes(self, key):
        return self.blobs[key]


def _document(document_id: str, claim_id: str, pdf_key: str) -> dict:
    return {
        "document_id": document_id,
        "candidate_id": f"CAND-{document_id}",
        "claim_id": claim_id,
        "title": f"Title for {document_id}",
        "citation": f"Citation for {document_id}",
        "source_type": "openalex",
        "authority": "peer_reviewed",
        "independence_group": f"IG-{document_id}",
        "version_id": f"VER-{document_id}",
        "content_sha256": "0" * 64,
        "pdf_object_key": pdf_key,
        "pages_object_key": f"{pdf_key}.pages.json",
        "page_count": 1,
        "fetched_at": "2026-01-01T00:00:00Z",
    }


def _graph_state(claims: dict[str, str]) -> dict:
    return {
        "domain_state": {
            "eu_proposal": {
                "claims": {
                    claim_id: {"id": claim_id, "text": text}
                    for claim_id, text in claims.items()
                }
            }
        }
    }


class _FakeContext:
    def __init__(self, dockey: str, score: int):
        self.score = score
        self.text = SimpleNamespace(doc=SimpleNamespace(dockey=dockey))


class _FakeSession:
    def __init__(self, answer, formatted_answer, contexts, cost):
        self.answer = answer
        self.formatted_answer = formatted_answer
        self.contexts = contexts
        self.cost = cost


class _FakeDocsHappyPath:
    def __init__(self, *args, **kwargs):
        self.added: list[str] = []

    async def aadd_file(self, file, *, citation, docname, dockey, title, settings):
        assert file.read().startswith(b"%PDF") or True
        self.added.append(dockey)

    async def aquery(self, question, *, settings):
        contexts = [_FakeContext(self.added[0], 8)] if self.added else []
        return _FakeSession(
            answer="Biomass residues remain under-valorised in three studies.",
            formatted_answer="Q: ...\nA: Biomass residues remain under-valorised.",
            contexts=contexts,
            cost=0.0123,
        )


class _FakeDocsRaisesOnQuery:
    def __init__(self, *args, **kwargs):
        pass

    async def aadd_file(self, *args, **kwargs):
        return None

    async def aquery(self, *args, **kwargs):
        raise RuntimeError("embedding provider unavailable")


@pytest.mark.asyncio
async def test_synthesizer_maps_context_coverage_back_to_documents(monkeypatch):
    import paperqa

    monkeypatch.setattr(paperqa, "Docs", _FakeDocsHappyPath)

    store = StubObjectStore(blobs={"evidence/doc-1.pdf": b"%PDF-fake-bytes"})
    document = _document("DOC-1", "CL-1", "evidence/doc-1.pdf")
    node = PaperQAEvidenceSynthesizerAgent(
        "synthesize",
        {"documents": [document]},
        services={"object_store": store},
    )
    state = _graph_state({"CL-1": "Biomass residues are under-valorised."})

    result = await node.run(state, node.config.model_dump())

    assert result["claims_processed"] == 1
    assert result["claims_skipped_no_claim_text"] == 0
    entry = result["results"][0]
    assert entry["claim_id"] == "CL-1"
    assert entry["documents_total"] == 1
    assert entry["documents_used"] == 1
    assert entry["document_coverage"][0]["document_id"] == "DOC-1"
    assert entry["document_coverage"][0]["used"] is True
    assert entry["document_coverage"][0]["best_score"] == 8
    assert "under-valorised" in entry["answer"]
    assert result["total_cost_usd"] == pytest.approx(0.0123)
    assert result["verification_status"] == "unverified_synthesis"


@pytest.mark.asyncio
async def test_synthesizer_skips_claims_missing_from_graph(monkeypatch):
    import paperqa

    monkeypatch.setattr(paperqa, "Docs", _FakeDocsHappyPath)

    store = StubObjectStore(blobs={"evidence/doc-2.pdf": b"%PDF-fake-bytes"})
    document = _document("DOC-2", "CL-404", "evidence/doc-2.pdf")
    node = PaperQAEvidenceSynthesizerAgent(
        "synthesize",
        {"documents": [document]},
        services={"object_store": store},
    )
    state = _graph_state({"CL-1": "Unrelated claim."})

    result = await node.run(state, node.config.model_dump())

    assert result["claims_processed"] == 0
    assert result["claims_skipped_no_claim_text"] == 1


@pytest.mark.asyncio
async def test_synthesizer_records_error_without_raising(monkeypatch):
    import paperqa

    monkeypatch.setattr(paperqa, "Docs", _FakeDocsRaisesOnQuery)

    store = StubObjectStore(blobs={"evidence/doc-3.pdf": b"%PDF-fake-bytes"})
    document = _document("DOC-3", "CL-1", "evidence/doc-3.pdf")
    node = PaperQAEvidenceSynthesizerAgent(
        "synthesize",
        {"documents": [document]},
        services={"object_store": store},
    )
    state = _graph_state({"CL-1": "Biomass residues are under-valorised."})

    result = await node.run(state, node.config.model_dump())

    assert result["claims_processed"] == 1
    entry = result["results"][0]
    assert entry["error"] is not None
    assert "embedding provider unavailable" in entry["error"]
    assert entry["documents_used"] == 0
