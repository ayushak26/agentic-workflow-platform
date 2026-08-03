"""Phase 1 coverage for cross-lane candidate deduplication.

The workflow fans three independent research lanes (scholarly search,
bounded deep research, prior-project retrieval) into ResearchSourceAcquirer.
Each lane previously deduped only against its OWN output, so the same paper
found by two lanes consumed two of a claim's scarce per-claim acquisition
slots and was fetched, stored and passage-verified twice.
"""
from __future__ import annotations

from app.evidence.models import CandidateSource
from app.evidence.retrieval import (
    candidate_work_identity,
    deduplicate_candidates,
)
from app.nodes.research_source_acquirer import _bounded_candidates

DOI_A = "10.1016/j.biombioe.2023.106789"
DOI_B = "10.1038/s41586-023-06456-z"


def cand(
    cid: str,
    claim: str,
    title: str,
    lane: str | None = None,
    *,
    ids: dict[str, str] | None = None,
    url: str | None = None,
    purpose: str = "discovery",
    source: str = "backend",
) -> CandidateSource:
    return CandidateSource(
        candidate_id=cid,
        claim_id=claim,
        query="q",
        purpose=purpose,
        source=source,
        title=title,
        canonical_url=url,
        canonical_identifiers=ids or {},
        discovery_lane=lane,
        independence_group="IG",
        retrieved_at="now",
    )


class TestCrossLaneIdentity:
    def test_same_doi_collapses_even_when_titles_and_urls_differ(self):
        """Must be driven by the DOI, not by title similarity — the titles
        here are deliberately unrelated."""
        a = cand(
            "c1", "CL-1", "Residue mapping accuracy in EU regions",
            "scholarly_search", ids={"doi": DOI_A}, url=f"https://doi.org/{DOI_A}",
        )
        b = cand(
            "c2", "CL-1", "An entirely different looking title",
            "deep_research", ids={"doi": DOI_A},
            url="https://sciencedirect.com/pii/S1",
        )
        assert candidate_work_identity(a) == candidate_work_identity(b)
        out = deduplicate_candidates([a, b])
        assert len(out) == 1

    def test_every_lane_that_found_the_work_is_recorded(self):
        a = cand("c1", "CL-1", "P", "scholarly_search", ids={"doi": DOI_A})
        b = cand("c2", "CL-1", "P", "deep_research", ids={"doi": DOI_A})
        c = cand("c3", "CL-1", "P", "prior_project", ids={"doi": DOI_A})
        out = deduplicate_candidates([a, b, c])
        assert len(out) == 1
        assert out[0].discovery_lane == (
            "scholarly_search,deep_research,prior_project"
        )

    def test_distinct_works_are_never_merged(self):
        a = cand("c1", "CL-1", "Paper about biomass", ids={"doi": DOI_A})
        b = cand("c2", "CL-1", "Paper about photovoltaics", ids={"doi": DOI_B})
        assert len(deduplicate_candidates([a, b])) == 2

    def test_dedup_is_scoped_per_claim(self):
        """The same paper is legitimately evidence for two different claims
        and must survive once per claim."""
        a = cand("c1", "CL-1", "P", ids={"doi": DOI_A})
        b = cand("c2", "CL-2", "P", ids={"doi": DOI_A})
        assert len(deduplicate_candidates([a, b])) == 2

    def test_legacy_candidate_without_canonical_identifiers_still_dedupes_on_doi(
        self,
    ):
        """Records created before canonical_identifiers existed carry only
        the flat `doi` field."""
        a = CandidateSource(
            candidate_id="c1", claim_id="CL-1", query="q", purpose="discovery",
            source="backend", title="P", doi=DOI_A, independence_group="IG",
            retrieved_at="now",
        )
        b = cand("c2", "CL-1", "Different title", ids={"doi": DOI_A})
        assert len(deduplicate_candidates([a, b])) == 1


class TestTitleAliasing:
    def test_same_title_merges_when_only_one_lane_resolved_an_identifier(self):
        """The residual gap identifier precedence alone cannot close: one
        lane has a DOI, the other only a landing-page URL."""
        a = cand(
            "c1", "CL-1", "A Specific Paper Title", "scholarly_search",
            url="https://publisher.example/article/1",
        )
        b = cand(
            "c2", "CL-1", "a specific paper title", "deep_research",
            ids={"doi": DOI_A},
        )
        out = deduplicate_candidates([a, b])
        assert len(out) == 1
        # The richer record (with identifiers) wins the merge.
        assert out[0].canonical_identifiers == {"doi": DOI_A}

    def test_placeholder_titles_never_merge_unrelated_records(self):
        a = cand("p1", "CL-1", "(title unavailable)", url="https://a.example/1")
        b = cand("p2", "CL-1", "(title unavailable)", url="https://b.example/2")
        assert len(deduplicate_candidates([a, b])) == 2


class TestContradictionPreservation:
    def test_contradiction_tag_survives_collision_with_discovery(self):
        a = cand("c1", "CL-1", "P", ids={"doi": DOI_A}, purpose="discovery")
        b = cand("c2", "CL-1", "P", ids={"doi": DOI_A}, purpose="contradiction")
        out = deduplicate_candidates([a, b])
        assert len(out) == 1
        assert out[0].purpose == "contradiction"


class TestAcquirerAppliesCrossLaneDedup:
    def test_bounded_candidates_dedupes_before_spending_per_claim_slots(self):
        """Regression guard for the actual pipeline bug: the acquirer is the
        fan-in point and previously never deduped, so duplicates from two
        lanes consumed two of the claim's slots."""
        duplicates = [
            cand("c1", "CL-1", "Same paper", "scholarly_search", ids={"doi": DOI_A}),
            cand("c2", "CL-1", "Same paper", "deep_research", ids={"doi": DOI_A}),
        ]
        distinct = cand("c3", "CL-1", "Another paper", "deep_research", ids={"doi": DOI_B})
        selected = _bounded_candidates(
            duplicates + [distinct], per_claim=2, total=10
        )
        # Without dedup this would be [dup, dup] and drop `distinct` entirely.
        identities = {candidate_work_identity(c) for c in selected}
        assert len(selected) == 2
        assert identities == {f"doi:{DOI_A}", f"doi:{DOI_B}"}

    def test_per_claim_and_total_caps_still_enforced_after_dedup(self):
        many = [
            cand(f"c{i}", "CL-1", f"Paper {i}", "deep_research",
                 ids={"doi": f"10.1016/j.test.2023.{i:05d}"})
            for i in range(10)
        ]
        assert len(_bounded_candidates(many, per_claim=3, total=10)) == 3
        assert len(_bounded_candidates(many, per_claim=10, total=4)) == 4
