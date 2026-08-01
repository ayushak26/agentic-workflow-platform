from __future__ import annotations

import pytest

from app.nodes.concept_freeze import ConceptFreezeAgent


def _alternative(concept_id: str, posture: str, score: float) -> dict:
    return {
        "id": concept_id,
        "posture": posture,
        "title": f"Title {concept_id}",
        "summary": "Summary",
        "scientific_advance": "Advance",
        "scope": "Scope",
        "composite_score": score,
    }


@pytest.mark.asyncio
async def test_concept_freeze_resolves_selected_id():
    node = ConceptFreezeAgent(
        "freeze_concept",
        {
            "alternatives": [
                _alternative("CON-CONSERVATIVE", "conservative", 60.0),
                _alternative("CON-BALANCED", "balanced", 78.5),
                _alternative("CON-AMBITIOUS", "ambitious", 55.0),
            ],
            "selected_concept_id": "CON-BALANCED",
        },
    )
    result = await node.run({}, node.config.model_dump())
    assert result["selected_concept"]["id"] == "CON-BALANCED"
    assert result["selected_concept"]["composite_score"] == 78.5


@pytest.mark.asyncio
async def test_concept_freeze_fails_closed_on_unknown_id():
    node = ConceptFreezeAgent(
        "freeze_concept",
        {
            "alternatives": [
                _alternative("CON-CONSERVATIVE", "conservative", 60.0),
                _alternative("CON-BALANCED", "balanced", 78.5),
                _alternative("CON-AMBITIOUS", "ambitious", 55.0),
            ],
            "selected_concept_id": "CON-TYPO",
        },
    )
    with pytest.raises(ValueError, match="does not match any"):
        await node.run({}, node.config.model_dump())
