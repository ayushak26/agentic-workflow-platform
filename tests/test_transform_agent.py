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


@pytest.mark.asyncio
async def test_new_style_no_output_fields_returns_raw(stub_llm):
    stub_llm.queue("A concise summary of the document.")

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="summarize",
        raw_config={
            "model": "m",
            "instructions": "Summarize this document faithfully.",
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
async def test_new_style_structured_output_with_enum(stub_llm):
    stub_llm.queue(
        '{"intent": "SPARE_PARTS", "confidence": 0.9}'
    )

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="understand_message",
        raw_config={
            "model": "m",
            "instructions": "Classify the customer's intent.",
            "input_fields": [
                {
                    "name": "message",
                    "description": "Customer's full message",
                    "type": "string",
                    "value": "A price question about a pump I already own.",
                },
            ],
            "output_fields": [
                {
                    "name": "intent",
                    "type": "enum",
                    "description": "Main customer request",
                    "enum_values": ["QUOTATION", "SPARE_PARTS", "OTHER"],
                },
                {
                    "name": "confidence",
                    "type": "number",
                    "description": "Confidence 0.0-1.0",
                },
            ],
        },
        services={"llm": stub_llm},
    )

    result = await node.run(
        state={},
        resolved_config=node.config.model_dump(),
    )

    assert result["parsed"]["intent"] == "SPARE_PARTS"
    assert result["parsed"]["confidence"] == 0.9
    assert stub_llm.calls[0]["method"] == "complete_structured"


def test_new_style_user_prompt_embeds_resolved_input_value():
    from app.nodes.transform import TransformConfig

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="understand_message",
        raw_config={
            "model": "m",
            "instructions": "Classify the customer's intent.",
            "input_fields": [
                {
                    "name": "message",
                    "description": "Customer's full message",
                    "type": "string",
                    # In a real run, the executor's template resolver has
                    # already substituted the {{inputs.message}} placeholder
                    # with the real content before run() sees this value.
                    "value": "A price question about a pump I already own.",
                },
            ],
        },
        services={},
    )
    cfg = TransformConfig(**node.config.model_dump())
    prompt = node._new_style_user_prompt(cfg)

    # The resolved input value (not the template placeholder) reaches the
    # model — no raw {{inputs.x}} syntax leaks into the prompt.
    assert "already own" in prompt
    assert "{{inputs" not in prompt


@pytest.mark.asyncio
async def test_new_style_structured_failure_retries_then_raises(stub_llm):
    stub_llm.queue("not valid json")
    stub_llm.queue("still not valid json")

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="t",
        raw_config={
            "model": "m",
            "instructions": "Extract a number.",
            "output_fields": [{"name": "x", "type": "integer"}],
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


@pytest.mark.asyncio
async def test_new_style_reject_empty_fields_retries_literal_null_answer(stub_llm):
    stub_llm.queue('{"answer": "null"}')
    stub_llm.queue('{"answer": "Python, SQL, product discovery, RAG, MCP, and stakeholder communication."}')

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="answer",
        raw_config={
            "model": "m",
            "instructions": "Extract the important skills from the resume.",
            "input_fields": [{"name": "source_text", "value": "Resume skills: Python, SQL, RAG, MCP"}],
            "output_fields": [{"name": "answer", "type": "text", "required": True}],
            "reject_empty_fields": ["answer"],
        },
        services={"llm": stub_llm},
    )

    result = await node.run(state={}, resolved_config=node.config.model_dump())

    assert result["parsed"]["answer"].startswith("Python, SQL")
    assert len(stub_llm.calls) == 2
    assert all(call["method"] == "complete_structured" for call in stub_llm.calls)


def test_config_requires_instructions_or_prompt_template():
    from pydantic import ValidationError

    from app.nodes.transform import TransformConfig

    with pytest.raises(ValidationError):
        TransformConfig(model="m")


def test_new_style_input_field_accepts_none_for_an_unsupplied_optional():
    """A {{inputs.x}} reference to an optional workflow input that wasn't
    supplied at run time resolves to None (whole-value template mode
    preserves type) — this must not be a config validation error, and must
    render as an empty gap rather than the literal word "None"."""
    from app.nodes.transform import TransformConfig

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="t",
        raw_config={
            "model": "m",
            "instructions": "Do the thing.",
            "input_fields": [{"name": "subject", "value": None}],
        },
        services={},
    )
    cfg = TransformConfig(**node.config.model_dump())
    prompt = node._new_style_user_prompt(cfg)

    assert "subject: " in prompt
    assert "None" not in prompt


def test_new_style_input_field_accepts_a_whole_object_reference():
    """{{inputs.proposal_blueprint.specific_objectives_table}}-style references
    into a JSON-typed workflow input or an upstream node's object/list output
    resolve to a real dict/list at runtime (whole-value template mode), not a
    string — this must render as readable JSON, not a Python dict repr."""
    from app.nodes.transform import TransformConfig

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="t",
        raw_config={
            "model": "m",
            "instructions": "Draft the section.",
            "input_fields": [
                {
                    "name": "specific_objectives_table",
                    "value": [{"so_id": "SO1", "objective": "Reduce cost"}],
                },
            ],
        },
        services={},
    )
    cfg = TransformConfig(**node.config.model_dump())
    prompt = node._new_style_user_prompt(cfg)

    assert '"so_id": "SO1"' in prompt
    assert "{'so_id'" not in prompt  # not a Python repr