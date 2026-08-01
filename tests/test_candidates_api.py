from __future__ import annotations

from app.api.candidates import (
    _discovered_candidates_from_run,
    _discovery_candidate_url,
)


def test_discovery_candidate_url_prefers_canonical_then_pdf_then_doi():
    assert _discovery_candidate_url({"canonical_url": "https://a.example/paper"}) == (
        "https://a.example/paper"
    )
    assert _discovery_candidate_url({"pdf_url": "https://b.example/paper.pdf"}) == (
        "https://b.example/paper.pdf"
    )
    assert (
        _discovery_candidate_url({"doi": "10.1234/xyz"})
        == "https://doi.org/10.1234/xyz"
    )
    assert _discovery_candidate_url({}) is None


def test_discovered_candidates_from_run_dedupes_across_discovery_and_deep_research():
    run = {
        "node_runs": {
            "discover_candidates": {
                "node_id": "discover_candidates",
                "type_name": "ScholarlyCandidateDiscoveryAgent",
                "output": {
                    "candidates": [
                        {
                            "candidate_id": "CAND-1",
                            "claim_id": "CL-1",
                            "title": "Residue mapping accuracy",
                            "canonical_url": "https://journal.example/paper-1",
                            "doi": "10.1000/one",
                            "source": "openalex",
                            "purpose": "discovery",
                            "authority": "peer_reviewed",
                            "retraction_status": "clear",
                        },
                    ]
                },
            },
            "deep_research": {
                "node_id": "deep_research",
                "type_name": "BoundedDeepResearchAgent",
                "output": {
                    "candidates": [
                        # Same candidate_id already seen via discovery — must
                        # not appear twice.
                        {
                            "candidate_id": "CAND-1",
                            "claim_id": "CL-1",
                            "title": "Residue mapping accuracy",
                            "canonical_url": "https://journal.example/paper-1",
                        },
                        {
                            "candidate_id": "CAND-2",
                            "claim_id": "CL-2",
                            "title": "EU biomass policy overview",
                            "canonical_url": None,
                            "pdf_url": None,
                            "doi": None,
                            "source": "web",
                            "purpose": "discovery",
                            "authority": "unverified",
                            "retraction_status": "unchecked",
                        },
                    ]
                },
            },
            # A non-discovery node's output must be ignored even if it
            # happens to have a "candidates"-shaped field.
            "verify_evidence": {
                "node_id": "verify_evidence",
                "type_name": "ProposalEvidenceFactoryAgent",
                "output": {"candidates": [{"candidate_id": "CAND-SHOULD-NOT-APPEAR"}]},
            },
        }
    }

    candidates = _discovered_candidates_from_run(run)

    assert [c["candidate_id"] for c in candidates] == ["CAND-1", "CAND-2"]
    first = candidates[0]
    assert first["title"] == "Residue mapping accuracy"
    assert first["url"] == "https://journal.example/paper-1"
    assert first["found_by_type"] == "ScholarlyCandidateDiscoveryAgent"

    second = candidates[1]
    assert second["title"] == "EU biomass policy overview"
    assert second["url"] is None
    assert second["found_by_type"] == "BoundedDeepResearchAgent"


def test_discovered_candidates_from_run_handles_missing_node_runs():
    assert _discovered_candidates_from_run({}) == []
    assert _discovered_candidates_from_run({"node_runs": {}}) == []
