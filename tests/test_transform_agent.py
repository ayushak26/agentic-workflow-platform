import pytest
from app.nodes.registry import NodeRegistry


@pytest.mark.asyncio
async def test_transform_no_schema_returns_raw(stub_llm):
    """Free-text transform: no output_schema → raw text, empty parsed."""
    stub_llm.queue("A concise summary of the document.")

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="summarize",
        raw_config={"model": "m", "prompt_template": "Summarize this"},
        services={"llm": stub_llm},
    )
    result = await node.run(state={}, resolved_config=node.config.model_dump())

    assert result["raw"] == "A concise summary of the document."
    assert result["parsed"] == {}
    # Free-text path uses complete(), not complete_structured().
    assert stub_llm.calls[0]["method"] == "complete"


@pytest.mark.asyncio
async def test_transform_with_output_schema_returns_parsed(stub_llm):
    """Structured transform: output_schema → complete_structured → parsed dict."""
    stub_llm.queue('{"industry": "finance", "requirements": ["a", "b"]}')

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="rfp_intel",
        raw_config={
            "model": "claude-sonnet-4-5",
            "prompt_template": "Extract from RFP",
            "output_schema": {"industry": "str", "requirements": "list"},
        },
        services={"llm": stub_llm},
    )
    result = await node.run(state={}, resolved_config=node.config.model_dump())

    assert result["parsed"]["industry"] == "finance"
    assert result["parsed"]["requirements"] == ["a", "b"]
    # Structured path uses the provider's native structured-output mode.
    assert stub_llm.calls[0]["method"] == "complete_structured"


@pytest.mark.asyncio
async def test_transform_structured_failure_returns_empty(stub_llm):
    """If structured output fails (provider returns something unparseable), the node
    returns empty parsed rather than crashing. Downstream templating then fails
    loudly on the missing key — a deliberate fail-fast design."""
    stub_llm.queue("not valid json")

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="t",
        raw_config={
            "model": "m",
            "prompt_template": "p",
            "output_schema": {"x": "int"},
        },
        services={"llm": stub_llm},
    )
    result = await node.run(state={}, resolved_config=node.config.model_dump())

    assert result["parsed"] == {}
    assert result["raw"] == ""