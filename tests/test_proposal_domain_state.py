import json

import pytest

from app.nodes.graph_normalizer import GraphNormalizer
from app.proposal_graph import PROPOSAL_NAMESPACE
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import Claim, Objective
from app.proposal_graph.state import (
    proposal_graph_from_state,
    proposal_graph_state_update,
)
from app.runtime.domain_state import merge_domain_state


def test_proposal_graph_is_namespaced_outside_core_state():
    first_graph = ProposalGraph(
        claims={
            "CL-1": Claim(
                id="CL-1",
                text="Existing methods are incomplete.",
            )
        }
    )

    second_graph = ProposalGraph(
        objectives={
            "OBJ-1": Objective(
                id="OBJ-1",
                text="Build a validated alternative.",
            )
        }
    )

    state = {
        "domain_state": merge_domain_state(
            proposal_graph_state_update(first_graph)["domain_state"],
            proposal_graph_state_update(second_graph)["domain_state"],
        )
    }

    graph = proposal_graph_from_state(state)

    assert PROPOSAL_NAMESPACE == "eu_proposal"
    assert set(graph.claims) == {"CL-1"}
    assert set(graph.objectives) == {"OBJ-1"}


def test_graph_normalizer_rejects_invalid_model_json():
    with pytest.raises(
        ValueError,
        match="invalid JSON",
    ):
        GraphNormalizer._parse_json("this is not json")


@pytest.mark.asyncio
async def test_graph_normalizer_uses_provider_structured_output(stub_llm):
    stub_llm.queue(
        json.dumps(
            {
                "call_requirements": [],
                "objectives": [
                    {
                        "id": "OBJ-SO1",
                        "text": "Validate the proposed method.",
                        "work_package_ids": ["WP-1"],
                    }
                ],
                "work_packages": [
                    {
                        "id": "WP-1",
                        "number": 1,
                        "title": "Validation",
                        "objective_ids": ["OBJ-SO1"],
                    }
                ],
                "partners": [],
                "claims": [
                    {
                        "id": "CL-1",
                        "text": "Existing methods are incomplete.",
                        "claim_type": "problem",
                    }
                ],
                "open_questions": [],
            }
        )
    )

    node = GraphNormalizer(
        "concept_normalize",
        {
            "model": "gpt-5",
            "max_tokens": 8192,
        },
        {
            "llm": stub_llm,
        },
    )

    output = await node.run(
        {
            "inputs": {
                "concept_note": "Existing methods are incomplete.",
            }
        },
        node.config.model_dump(),
    )

    assert stub_llm.calls[0]["method"] == "complete_structured"
    assert output["counts"]["claims"] == 1
    assert output["counts"]["objectives"] == 1

    graph = proposal_graph_from_state(output["__state__"])

    assert (
        graph.claims["CL-1"].text
        == "Existing methods are incomplete."
    )