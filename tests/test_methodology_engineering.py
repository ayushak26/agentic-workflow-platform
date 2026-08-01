from __future__ import annotations

import json

import pytest

from app.nodes.methodology_engineering import MethodologyEngineeringAgent


def _graph_state(objectives: dict[str, str], claims: dict[str, str]) -> dict:
    return {
        "domain_state": {
            "eu_proposal": {
                "objectives": {
                    obj_id: {"id": obj_id, "text": text}
                    for obj_id, text in objectives.items()
                },
                "claims": {
                    claim_id: {"id": claim_id, "text": text}
                    for claim_id, text in claims.items()
                },
            }
        }
    }


class _StubLLM:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete_structured(self, *, model, system, user, response_model, **kwargs):
        self.calls.append({"model": model, "system": system, "user": user})
        return response_model.model_validate(self.responses.pop(0))


class _StubCatalog:
    enabled = True

    def select(self, *, objective, auto_select, max_skills):
        return _StubSelection(["geomaster"] if "territorial" in objective else ["pymoo"])

    def prompt_bundle(self, selection):
        return f"guidance for {','.join(selection.names)}"


class _StubSelection:
    def __init__(self, names):
        self.names = names


@pytest.mark.asyncio
async def test_methodology_engineering_produces_one_card_per_objective():
    llm = _StubLLM(
        [
            {
                "research_question_id": "RQ-1",
                "inputs": ["Sentinel-2 imagery"],
                "method": "Land-use change detection.",
                "baseline_or_comparator": "Prior CORINE land-cover map.",
                "validation": "Ground-truth sampling.",
                "uncertainty_method": "Confusion matrix.",
                "failure_condition": "Kappa below 0.6.",
                "responsible_capability": "WP2 geospatial team.",
                "evidence_claim_ids": ["CL-1", "CL-HALLUCINATED"],
                "status": "drafted",
            },
            {
                "research_question_id": "RQ-HALLUCINATED",
                "inputs": ["Trade-off scenarios"],
                "method": "Multi-objective optimisation of biomass allocation.",
                "baseline_or_comparator": "",
                "validation": "",
                "uncertainty_method": "",
                "failure_condition": "",
                "responsible_capability": "",
                "evidence_claim_ids": [],
                "status": "information_required",
            },
        ]
    )
    node = MethodologyEngineeringAgent(
        "methodology",
        {"research_briefs": [
            {
                "brief_id": "RQ-1",
                "track": "environment_climate_biodiversity",
                "question": "Which land-use datasets are available?",
                "purpose": "methodology_selection",
            }
        ]},
        services={"llm": llm, "scientific_skill_catalog": _StubCatalog()},
    )
    state = _graph_state(
        objectives={
            "OBJ-1": "Map territorial land-use change for biomass residues.",
            "OBJ-2": "Optimise trade-offs between biomass uses.",
        },
        claims={"CL-1": "Biomass residues are under-valorised."},
    )

    result = await node.run(state, node.config.model_dump())

    assert result["objectives_processed"] == 2
    cards = {card["supports_objective"]: card for card in result["method_cards"]}
    assert cards["OBJ-1"]["research_question_id"] == "RQ-1"
    assert cards["OBJ-1"]["evidence_claim_ids"] == ["CL-1"]
    assert cards["OBJ-1"]["selected_skills"] == ["geomaster"]
    assert cards["OBJ-2"]["research_question_id"] == ""
    assert cards["OBJ-2"]["status"] == "information_required"
    assert cards["OBJ-2"]["selected_skills"] == ["pymoo"]


@pytest.mark.asyncio
async def test_methodology_engineering_requires_objectives():
    node = MethodologyEngineeringAgent(
        "methodology",
        {},
        services={"llm": _StubLLM([])},
    )
    with pytest.raises(ValueError, match="no objectives"):
        await node.run({"domain_state": {"eu_proposal": {}}}, node.config.model_dump())
