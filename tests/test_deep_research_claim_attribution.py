"""Phase 1 coverage for per-claim citation attribution in the deep-research lane.

The bug: `_candidate_records` looped `for claim_id in dossier.linked_claim_ids`
and built a CandidateSource for EVERY claim in the brief from EVERY citation.
A brief covering four claims turned one source into four "evidence"
candidates, contaminating claim-source links before verification and burning
each claim's scarce acquisition budget on sources that had nothing to do with
it.
"""
from __future__ import annotations

from app.nodes.bounded_deep_research_agent import _candidate_records
from app.research.deep_research import (
    ResearchCitation,
    ResearchDossier,
    ResearchUsage,
)

DOI = "10.1016/j.biombioe.2023.106789"


def dossier(citations: list[ResearchCitation], claim_ids: list[str]) -> ResearchDossier:
    return ResearchDossier(
        brief_id="RB-1",
        track="state_of_art",
        question="Q",
        linked_claim_ids=claim_ids,
        model="gpt-5.6-sol",
        response_id="RESP-1",
        status="completed",
        report_markdown="report",
        citations=citations,
        usage=ResearchUsage(),
    )


def citation(
    cid: str,
    url: str,
    *,
    claim_id: str | None = None,
    stance: str | None = None,
    cited_text: str = "some text",
) -> ResearchCitation:
    return ResearchCitation(
        citation_id=cid,
        title=f"Title {cid}",
        url=url,
        cited_text=cited_text,
        claim_id=claim_id,
        stance=stance,
    )


def records(d: ResearchDossier):
    return _candidate_records(
        [d], max_citations_per_brief=20, max_candidates_per_claim=20
    )


class TestPerClaimAttribution:
    def test_attributed_citation_creates_exactly_one_candidate(self):
        """The core fix: one source supporting one claim yields ONE
        candidate, not one per claim in the brief."""
        d = dossier(
            [citation("x1", "https://a.example/1", claim_id="CL-2",
                      stance="supports")],
            ["CL-1", "CL-2", "CL-3", "CL-4"],
        )
        out = records(d)
        assert len(out) == 1
        assert out[0].claim_id == "CL-2"

    def test_each_citation_goes_to_its_own_claim(self):
        d = dossier(
            [
                citation("x1", "https://a.example/1", claim_id="CL-1", stance="supports"),
                citation("x2", "https://b.example/2", claim_id="CL-3", stance="supports"),
            ],
            ["CL-1", "CL-2", "CL-3"],
        )
        out = records(d)
        assert {(c.claim_id, c.canonical_url) for c in out} == {
            ("CL-1", "https://a.example/1"),
            ("CL-3", "https://b.example/2"),
        }
        # CL-2 got nothing, correctly — no citation spoke to it.
        assert not [c for c in out if c.claim_id == "CL-2"]

    def test_single_claim_brief_needs_no_attribution(self):
        d = dossier([citation("x1", "https://a.example/1")], ["CL-9"])
        out = records(d)
        assert len(out) == 1
        assert out[0].claim_id == "CL-9"
        assert out[0].metadata_status == "candidate"

    def test_unattributed_multi_claim_citation_is_marked_unresolved(self):
        """Attribution can legitimately fail (gateway without structured
        output). Recall is preserved, but the broad attribution is flagged so
        downstream fusion can prefer precise ones."""
        d = dossier(
            [citation("x1", "https://a.example/1")], ["CL-1", "CL-2"]
        )
        out = records(d)
        assert {c.claim_id for c in out} == {"CL-1", "CL-2"}
        assert all(c.metadata_status == "unresolved" for c in out)


class TestStanceDrivesPurpose:
    def test_contradicting_stance_marks_contradiction_purpose(self):
        d = dossier(
            [citation("x1", "https://a.example/1", claim_id="CL-1",
                      stance="contradicts")],
            ["CL-1"],
        )
        assert records(d)[0].purpose == "contradiction"

    def test_qualifying_stance_also_counts_as_contradiction_evidence(self):
        d = dossier(
            [citation("x1", "https://a.example/1", claim_id="CL-1",
                      stance="qualifies")],
            ["CL-1"],
        )
        assert records(d)[0].purpose == "contradiction"

    def test_supporting_stance_is_discovery(self):
        d = dossier(
            [citation("x1", "https://a.example/1", claim_id="CL-1",
                      stance="supports", cited_text="a study of limitations")],
            ["CL-1"],
        )
        # Note the snippet contains "limitations" -- the old keyword scan
        # would have misfiled this as contradiction evidence. A resolved
        # stance must win over the substring heuristic.
        assert records(d)[0].purpose == "discovery"

    def test_keyword_fallback_only_applies_without_a_stance(self):
        d = dossier(
            [citation("x1", "https://a.example/1", claim_id="CL-1",
                      cited_text="we found no significant effect")],
            ["CL-1"],
        )
        assert records(d)[0].purpose == "contradiction"


class TestIdentifiersAndLane:
    def test_publisher_url_doi_is_resolved_onto_the_candidate(self):
        d = dossier(
            [citation("x1", f"https://link.springer.com/article/{DOI}",
                      claim_id="CL-1")],
            ["CL-1"],
        )
        out = records(d)[0]
        assert out.canonical_identifiers.get("doi") == DOI
        assert out.doi == DOI

    def test_lane_is_labelled_for_cross_lane_dedup(self):
        d = dossier([citation("x1", "https://a.example/1", claim_id="CL-1")], ["CL-1"])
        assert records(d)[0].discovery_lane == "deep_research"

    def test_doi_alone_does_not_claim_peer_review(self):
        d = dossier(
            [citation("x1", f"https://doi.org/{DOI}", claim_id="CL-1")], ["CL-1"]
        )
        assert records(d)[0].authority == "scholarly_status_unconfirmed"

    def test_preprint_host_classified_as_preprint(self):
        d = dossier(
            [citation("x1", "https://arxiv.org/abs/2401.12345", claim_id="CL-1")],
            ["CL-1"],
        )
        assert records(d)[0].authority == "preprint"
