from __future__ import annotations

from app.evidence.retrieval import candidate_from_paper, deduplicate_candidates


def _paper(**overrides):
    base = {
        "paper_id": None,
        "title": "Residue mapping accuracy improves regional bioeconomy planning",
        "doi": None,
        "source": "openalex",
        "authors": "A. Researcher",
        "published_date": "2025-01-01",
    }
    base.update(overrides)
    return base


def test_dedup_collapses_cosmetically_different_titles_from_different_backends():
    """The same paper, returned by two backends with cosmetic title
    differences (trailing period, curly quote, double space), used to
    survive as two "different" candidates because the fallback identity was
    a raw .lower() with no normalisation."""
    candidates = [
        candidate_from_paper(
            _paper(
                title="Residue Mapping Accuracy Improves Regional Bioeconomy Planning.",
                source="openalex",
            ),
            claim_id="CL-1",
            query="q1",
            purpose="discovery",
        ),
        candidate_from_paper(
            _paper(
                title="Residue mapping accuracy improves regional  bioeconomy planning",
                source="core",
            ),
            claim_id="CL-1",
            query="q2",
            purpose="discovery",
        ),
    ]

    result = deduplicate_candidates(candidates)

    assert len(result) == 1


def test_dedup_prefers_doi_over_title_when_available():
    candidates = [
        candidate_from_paper(
            _paper(title="Title as indexed by source A", doi="10.1000/xyz"),
            claim_id="CL-1",
            query="q1",
            purpose="discovery",
        ),
        candidate_from_paper(
            # Same DOI, materially different title string (e.g. a
            # publisher-vs-preprint title variant) — must still collapse.
            _paper(title="A completely different-looking title string", doi="10.1000/xyz"),
            claim_id="CL-1",
            query="q2",
            purpose="discovery",
        ),
    ]

    result = deduplicate_candidates(candidates)

    assert len(result) == 1
    assert result[0].doi == "10.1000/xyz"


def test_dedup_merges_same_link_and_same_source_when_no_doi_or_paper_id():
    """The most common real duplicate: no DOI/paper_id populated (routine
    for Zenodo/HAL/DOAJ/CORE-style backends), but two query variants hit the
    same backend and get back the identical canonical_url. That must
    collapse even though titles are phrased slightly differently."""
    candidates = [
        candidate_from_paper(
            _paper(
                title="Biomass residue governance in EU regions",
                source="hal",
                url="https://hal.example/12345",
            ),
            claim_id="CL-1",
            query="q1",
            purpose="discovery",
        ),
        candidate_from_paper(
            _paper(
                title="Biomass Residue Governance in EU Regions (preprint)",
                source="hal",
                url="https://hal.example/12345",
            ),
            claim_id="CL-1",
            query="q2 phrased differently",
            purpose="discovery",
        ),
    ]

    result = deduplicate_candidates(candidates)

    assert len(result) == 1


def test_dedup_merges_discovery_and_contradiction_hits_keeping_contradiction_tag():
    """Fanning discovery and contradiction queries out to the same backends
    commonly surfaces the same paper for both purposes — previously kept as
    two separate candidates since purpose was part of the identity key."""
    candidates = [
        candidate_from_paper(
            _paper(doi="10.1000/same"),
            claim_id="CL-1",
            query="discovery query",
            purpose="discovery",
        ),
        candidate_from_paper(
            _paper(doi="10.1000/same"),
            claim_id="CL-1",
            query="contradiction query",
            purpose="contradiction",
        ),
    ]

    result = deduplicate_candidates(candidates)

    assert len(result) == 1
    assert result[0].purpose == "contradiction"


def test_dedup_does_not_merge_the_same_paper_across_different_claims():
    """A candidate is intrinsically claim-scoped — the same source being
    relevant to two distinct claims is not duplication."""
    candidates = [
        candidate_from_paper(
            _paper(doi="10.1000/shared"),
            claim_id="CL-1",
            query="q1",
            purpose="discovery",
        ),
        candidate_from_paper(
            _paper(doi="10.1000/shared"),
            claim_id="CL-2",
            query="q1",
            purpose="discovery",
        ),
    ]

    result = deduplicate_candidates(candidates)

    assert len(result) == 2
    assert {item.claim_id for item in result} == {"CL-1", "CL-2"}
