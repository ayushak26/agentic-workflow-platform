# tests/test_collections.py
import pytest
from app.ingestion.collections import CollectionConfig


@pytest.fixture
def proposal_cfg() -> CollectionConfig:
    return CollectionConfig(
        collection_id="proposal",
        display_name="Proposal Generation",
        doc_types=["proposal", "case_study", "methodology"],
    )


def test_accepts_declared_doc_type(proposal_cfg):
    proposal_cfg.validate_doc_type("case_study")  # no raise


def test_rejects_unknown_doc_type(proposal_cfg):
    with pytest.raises(ValueError, match="not declared in collection 'proposal'"):
        proposal_cfg.validate_doc_type("meeting_note")


def test_validates_list_and_names_the_bad_one(proposal_cfg):
    with pytest.raises(ValueError, match="meeting_note"):
        proposal_cfg.validate_doc_types(["proposal", "meeting_note"])


def test_empty_vocabulary_rejected():
    with pytest.raises(ValueError):  # Field(min_length=1)
        CollectionConfig(collection_id="x", display_name="X", doc_types=[])