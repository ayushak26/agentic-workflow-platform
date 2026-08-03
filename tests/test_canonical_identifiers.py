"""Phase 1 coverage for app/evidence/identifiers.py.

Replaces the old `_doi_from_url` in bounded_deep_research_agent.py, which
only recognised a bare doi.org/dx.doi.org hostname — so a DOI sitting in a
publisher landing-page URL was invisible, the same work arriving from
different lanes got different identities (defeating dedup), and every
DOI-bearing source was promoted to "peer_reviewed" on no evidence.
"""
from __future__ import annotations

from app.evidence.identifiers import (
    ResolvedWork,
    classify_authority,
    extract_identifiers,
    normalise_doi_value,
    normalise_title,
    work_identity,
)


class TestDoiExtraction:
    def test_doi_org_url(self):
        assert extract_identifiers(
            "https://doi.org/10.1016/j.biombioe.2023.106789"
        ).doi == "10.1016/j.biombioe.2023.106789"

    def test_dx_doi_org_url(self):
        assert extract_identifiers(
            "https://dx.doi.org/10.1038/s41586-023-06456-z"
        ).doi == "10.1038/s41586-023-06456-z"

    def test_publisher_landing_page_with_doi_in_path(self):
        """The case the old hostname-only check missed entirely."""
        assert extract_identifiers(
            "https://link.springer.com/article/10.1007/s10021-023-00845-1"
        ).doi == "10.1007/s10021-023-00845-1"

    def test_doi_from_record_metadata_when_url_has_none(self):
        work = extract_identifiers(
            "https://www.mdpi.com/2071-1050/15/12/9432",
            metadata={"citation_doi": "10.3390/su15129432"},
        )
        assert work.doi == "10.3390/su15129432"

    def test_doi_is_lowercased_for_stable_identity(self):
        assert normalise_doi_value("10.1016/J.BiomBioe.2023.1") == (
            "10.1016/j.biombioe.2023.1"
        )

    def test_trailing_punctuation_is_stripped(self):
        assert normalise_doi_value("see 10.1234/abc.def).") == "10.1234/abc.def"

    def test_no_doi_returns_none(self):
        assert extract_identifiers("https://example.com/blog/post").doi is None
        assert normalise_doi_value(None) is None
        assert normalise_doi_value("") is None


class TestOtherIdentifiers:
    def test_arxiv_abs_url_with_and_without_version(self):
        assert extract_identifiers("https://arxiv.org/abs/2401.12345").arxiv_id == (
            "2401.12345"
        )
        assert extract_identifiers(
            "https://arxiv.org/abs/2401.12345v3"
        ).arxiv_id == "2401.12345"

    def test_arxiv_doi_prefix_yields_arxiv_id(self):
        work = extract_identifiers("https://doi.org/10.48550/arXiv.2401.12345")
        assert work.arxiv_id == "2401.12345"

    def test_pubmed_and_pmc(self):
        assert extract_identifiers(
            "https://pubmed.ncbi.nlm.nih.gov/37512345/"
        ).pmid == "37512345"
        assert extract_identifiers(
            "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10234567/"
        ).pmcid == "PMC10234567"

    def test_openalex_and_semantic_scholar(self):
        assert extract_identifiers(
            "https://openalex.org/W2741809807"
        ).openalex_id == "W2741809807"
        assert extract_identifiers(
            "https://www.semanticscholar.org/paper/"
            "53c9f3c34d8481adaf24df3b25581ccf1bc53f5c"
        ).semantic_scholar_id == "53c9f3c34d8481adaf24df3b25581ccf1bc53f5c"

    def test_generic_paper_id_is_not_treated_as_semantic_scholar_id(self):
        """Every backend has a `paper_id`; trusting it unconditionally would
        mint bogus S2 ids and therefore bogus dedup collisions."""
        work = extract_identifiers(
            "https://arxiv.org/abs/2401.12345",
            metadata={"source": "arxiv", "paper_id": "2401.12345v1"},
        )
        assert work.semantic_scholar_id is None
        assert work.arxiv_id == "2401.12345"

    def test_semantic_backend_record_paper_id_is_accepted(self):
        work = extract_identifiers(
            "https://www.semanticscholar.org/paper/x",
            metadata={
                "source": "semantic",
                "paper_id": "53c9f3c34d8481adaf24df3b25581ccf1bc53f5c",
            },
        )
        assert work.semantic_scholar_id == (
            "53c9f3c34d8481adaf24df3b25581ccf1bc53f5c"
        )


class TestWorkIdentity:
    def test_same_doi_different_urls_and_titles_collide(self):
        """The core cross-lane dedup guarantee."""
        a = extract_identifiers("https://doi.org/10.1016/j.test.2023.001")
        b = extract_identifiers(
            "https://sciencedirect.com/pii/S1", metadata={"doi": "10.1016/j.test.2023.001"}
        )
        assert work_identity(a, title="Some Title") == work_identity(
            b, title="A completely different title string"
        )

    def test_different_dois_do_not_collide(self):
        a = extract_identifiers("https://doi.org/10.1016/j.test.2023.001")
        b = extract_identifiers("https://doi.org/10.1038/s41586-023-99999")
        assert work_identity(a, title="T") != work_identity(b, title="T")

    def test_falls_back_to_normalised_title_without_identifiers(self):
        bare = ResolvedWork()
        assert work_identity(bare, title="A Paper Title.") == work_identity(
            bare, title="a  paper   title"
        )

    def test_title_normalisation_collapses_cosmetic_differences(self):
        assert normalise_title("Residue Mapping Accuracy.") == (
            normalise_title("residue  mapping   accuracy")
        )


class TestAuthorityClassification:
    def test_doi_alone_does_not_imply_peer_review(self):
        """A DOI is also minted for datasets, preprints, editorials,
        corrections, conference abstracts, protocols and retracted items."""
        work = extract_identifiers("https://doi.org/10.1016/j.test.2023.001")
        _, authority = classify_authority("https://doi.org/10.1016/j.test.2023.001", work)
        assert authority == "scholarly_status_unconfirmed"
        assert authority != "peer_reviewed"

    def test_resolved_journal_metadata_does_imply_peer_review(self):
        work = ResolvedWork(
            doi="10.1016/j.test.2023.001",
            publication_type="journal-article",
            venue="Journal of Testing",
        )
        _, authority = classify_authority("https://doi.org/10.1016/j.test.2023.001", work)
        assert authority == "peer_reviewed"

    def test_preprint_host_is_preprint_not_peer_reviewed(self):
        work = extract_identifiers("https://arxiv.org/abs/2401.12345")
        source, authority = classify_authority(
            "https://arxiv.org/abs/2401.12345", work
        )
        assert (source, authority) == ("preprint", "preprint")

    def test_preprint_with_a_doi_is_still_a_preprint(self):
        work = ResolvedWork(doi="10.48550/arxiv.2401.00001", is_preprint=True)
        _, authority = classify_authority("https://arxiv.org/abs/2401.1", work)
        assert authority == "preprint"

    def test_retracted_is_surfaced_not_hidden(self):
        work = ResolvedWork(
            doi="10.1016/j.test.2023.001", publication_type="journal-article",
            venue="J", is_retracted=True,
        )
        _, authority = classify_authority("https://doi.org/10.1016/j.test.2023.001", work)
        assert authority == "retracted"

    def test_official_eu_host(self):
        work = extract_identifiers("https://cordis.europa.eu/project/id/1")
        assert classify_authority(
            "https://cordis.europa.eu/project/id/1", work
        ) == ("official_eu", "official_eu")

    def test_plain_web_page_is_unverified(self):
        work = extract_identifiers("https://example.com/blog")
        assert classify_authority("https://example.com/blog", work) == (
            "web",
            "unverified",
        )

    def test_is_peer_reviewed_requires_venue_or_publisher(self):
        """A bare type label on an otherwise-unknown item is not enough."""
        assert not ResolvedWork(
            doi="10.1016/j.test.2023.001", publication_type="journal-article"
        ).is_peer_reviewed()
        assert ResolvedWork(
            doi="10.1016/j.test.2023.001", publication_type="journal-article", publisher="Elsevier"
        ).is_peer_reviewed()
