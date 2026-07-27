import json

import pytest

from app.nodes.registry import NodeRegistry


@pytest.mark.asyncio
async def test_transform_no_schema_returns_raw(stub_llm):
    stub_llm.queue("A concise summary of the document.")

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="summarize",
        raw_config={
            "model": "m",
            "prompt_template": "Summarize this",
        },
        services={"llm": stub_llm},
    )

    result = await node.run(
        state={},
        resolved_config=node.config.model_dump(),
    )

    assert result["raw"] == "A concise summary of the document."
    assert result["parsed"] == {}
    assert stub_llm.calls[0]["method"] == "complete"


@pytest.mark.asyncio
async def test_transform_with_output_schema_returns_parsed(stub_llm):
    stub_llm.queue(
        '{"industry": "finance", "requirements": ["a", "b"]}'
    )

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="rfp_intel",
        raw_config={
            "model": "claude-sonnet-4-5",
            "prompt_template": "Extract from RFP",
            "output_schema": {
                "industry": "str",
                "requirements": "list",
            },
        },
        services={"llm": stub_llm},
    )

    result = await node.run(
        state={},
        resolved_config=node.config.model_dump(),
    )

    assert result["parsed"]["industry"] == "finance"
    assert result["parsed"]["requirements"] == ["a", "b"]
    assert stub_llm.calls[0]["method"] == "complete_structured"


@pytest.mark.asyncio
async def test_transform_decodes_json_encoded_object(stub_llm):
    ssh = {
        "status": "PARTIAL",
        "disciplines": ["economics"],
        "partners": [],
        "methods": ["stakeholder interviews"],
        "tasks": [],
        "gaps": ["Confirm the SSH partner."],
    }

    # Simulates Claude returning ssh as a JSON-encoded string.
    stub_llm.queue(
        json.dumps(
            {
                "ssh": json.dumps(ssh),
            }
        )
    )

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="draft_cross_cutting",
        raw_config={
            "model": "m",
            "prompt_template": "Produce SSH compliance data",
            "output_schema": {
                "ssh": "object",
            },
        },
        services={"llm": stub_llm},
    )

    result = await node.run(
        state={},
        resolved_config=node.config.model_dump(),
    )

    assert result["parsed"]["ssh"] == ssh
    assert isinstance(result["parsed"]["ssh"], dict)
    assert json.loads(result["raw"])["ssh"] == ssh


@pytest.mark.asyncio
async def test_transform_structured_failure_retries_then_raises(
    stub_llm,
):
    stub_llm.queue("not valid json")
    stub_llm.queue("still not valid json")

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="t",
        raw_config={
            "model": "m",
            "prompt_template": "p",
            "output_schema": {
                "x": "int",
            },
        },
        services={"llm": stub_llm},
    )

    with pytest.raises(
        RuntimeError,
        match="failed to produce valid structured output after 2 attempt",
    ):
        await node.run(
            state={},
            resolved_config=node.config.model_dump(),
        )

    assert len(stub_llm.calls) == 2
    assert all(
        call["method"] == "complete_structured"
        for call in stub_llm.calls
    )