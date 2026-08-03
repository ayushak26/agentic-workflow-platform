"""Authority weighting for verified evidence, incl. open-web/grey literature.

Two things this locks down:

1. Open-web and grey literature are first-class evidence for this domain.
   Official policy texts, standards, JRC/EEA/IEA technical reports,
   statistical datasets and funded-project implementation evidence are often
   the only authoritative sources for policy, market and regulatory claims.
   They were scored 8 (grey) / 4 (unclassified web) against 18 for peer
   review, which left web-derived evidence near "unusable" even after it had
   passed exact-passage verification.

2. ``scholarly_status_unconfirmed`` — introduced when "has a DOI" stopped
   being treated as proof of peer review — must not fall through to the
   unknown-value default. Doing so silently scored every DOI-bearing source
   as if it were an arbitrary web page (18 -> 4), and ``_authority()`` also
   coerced it to ``Authority.UNVERIFIED`` in the citation registry because it
   was missing from the enum.

Crucially, authority weighting only labels the strength of an ALREADY-VERIFIED
passage. It does not relax the verification boundary: the exact-locator,
stance and confidence terms are unchanged, retraction still floors the score,
and ProposalTruthGraphAgent's ``drafting_allowed`` keys on accepted claims,
evidence approval and blockers — never on this score.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.nodes.proposal_evidence_factory import (
    _authority,
    _evidence_score,
    _strength,
)
from app.proposal_graph.models import Authority


def _doc(authority: str, retraction: str = "clear") -> SimpleNamespace:
    return SimpleNamespace(authority=authority, retraction_status=retraction)


def _verified_score(authority: str) -> int:
    """Score for a well-evidenced, exactly-located, corroborated passage."""
    return _evidence_score(
        document=_doc(authority),
        stance="supports_directly",
        confidence=0.9,
        exact_locator=True,
        corroborated=True,
    )


class TestWebAndGreyAreFirstClass:
    def test_grey_literature_reaches_strong_when_properly_verified(self):
        assert _strength(_verified_score("grey")) == "strong"

    def test_unclassified_web_source_reaches_strong_when_properly_verified(self):
        assert _strength(_verified_score("unverified")) == "strong"

    def test_web_and_grey_outrank_a_self_asserted_partner_claim(self):
        """Third-party web evidence has a different (better) trust basis than
        a consortium partner asserting something about itself."""
        assert _verified_score("grey") > _verified_score("partner_claim")
        assert _verified_score("unverified") > _verified_score("partner_claim")

    def test_official_and_peer_reviewed_still_rank_highest(self):
        """Raising web/grey must not invert the ordering."""
        assert _verified_score("official_eu") >= _verified_score("peer_reviewed")
        assert _verified_score("peer_reviewed") > _verified_score("grey")
        assert _verified_score("grey") > _verified_score("preprint")


class TestScholarlyStatusUnconfirmed:
    def test_is_a_real_authority_value_not_coerced_to_unverified(self):
        assert (
            _authority("scholarly_status_unconfirmed")
            is Authority.SCHOLARLY_STATUS_UNCONFIRMED
        )

    def test_scores_above_preprint_and_below_confirmed_peer_review(self):
        score = _verified_score("scholarly_status_unconfirmed")
        assert score > _verified_score("preprint")
        assert score < _verified_score("peer_reviewed")

    def test_is_not_scored_as_an_unknown_value(self):
        assert _verified_score("scholarly_status_unconfirmed") > _verified_score(
            "some_unmapped_authority_string"
        )

    def test_retracted_is_a_real_authority_value(self):
        assert _authority("retracted") is Authority.RETRACTED


class TestVerificationBoundaryUnchanged:
    def test_missing_exact_locator_prevents_a_strong_label(self):
        """Raising authority must not let an unlocated passage look strong."""
        for authority in ("peer_reviewed", "grey", "unverified"):
            score = _evidence_score(
                document=_doc(authority),
                stance="supports_directly",
                confidence=0.3,
                exact_locator=False,
                corroborated=False,
            )
            assert _strength(score) != "strong"

    def test_retraction_floors_the_score_regardless_of_authority(self):
        for authority in ("peer_reviewed", "grey", "official_eu"):
            score = _evidence_score(
                document=_doc(authority, retraction="retracted"),
                stance="supports_directly",
                confidence=0.9,
                exact_locator=True,
                corroborated=True,
            )
            assert _strength(score) == "unusable"

    def test_failed_verification_never_reaches_moderate_or_strong(self):
        """The fail-closed path (verifier quote absent -> insufficient,
        confidence 0) must stay low no matter how authoritative the source."""
        for authority in ("official_eu", "peer_reviewed", "grey", "unverified"):
            score = _evidence_score(
                document=_doc(authority),
                stance="insufficient",
                confidence=0.0,
                exact_locator=True,
                corroborated=False,
            )
            assert _strength(score) in {"weak", "unusable"}

    def test_unmapped_authority_stays_conservative(self):
        assert _verified_score("totally_unknown") < _verified_score("preprint")
