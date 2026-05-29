"""TransformAgent tests using a stubbed LLM.

We don't exercise the full executor here — these are unit tests that call
the node's run() method directly with a fabricated state. The integration
test for TransformAgent via the executor lives in test_workflow_demos.py
(to be written when we wire the rfp_intel_demo workflow)."""
import app.nodes  # noqa: F401

from app.nodes.registry import NodeRegistry


async def test_transform_with_output_schema_parses_json(stub_llm):
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
    # Schema instructions were appended to the prompt
    user_msg = stub_llm.calls[0]["messages"][0]["content"]
    assert "JSON" in user_msg


async def test_transform_strips_markdown_fences(stub_llm):
    stub_llm.queue('```json\n{"x": 1}\n```')

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
    assert result["parsed"]["x"] == 1


async def test_transform_retries_on_bad_json(stub_llm):
    # First response: junk. Second response: valid JSON. Should succeed.
    stub_llm.queue("this is not json at all")
    stub_llm.queue('{"x": 42}')

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="t",
        raw_config={
            "model": "m",
            "prompt_template": "p",
            "output_schema": {"x": "int"},
            "max_retries": 1,
        },
        services={"llm": stub_llm},
    )
    result = await node.run(state={}, resolved_config=node.config.model_dump())
    assert result["parsed"]["x"] == 42
    assert len(stub_llm.calls) == 2


async def test_transform_no_schema_returns_raw(stub_llm):
    stub_llm.queue("free form summary")

    cls = NodeRegistry.get("TransformAgent")
    node = cls(
        node_id="t",
        raw_config={"model": "m", "prompt_template": "p"},
        services={"llm": stub_llm},
    )
    result = await node.run(state={}, resolved_config=node.config.model_dump())
    assert result["raw"] == "free form summary"
    assert result["parsed"] == {}