"""MCPAgent tests with a stubbed MCP client. The real subprocess wiring
is tested in test_mcp_server.py."""
import app.nodes  # noqa: F401
from app.nodes.registry import NodeRegistry
from app.llm.base import LLMToolUseResponse, ToolCall


class StubMCPTool:
    def __init__(self, name, description, inputSchema):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


class StubMCPClient:
    def __init__(self, tools, tool_responses):
        self._tools = tools
        self._responses = tool_responses
        self.calls = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, arguments):
        self.calls.append({"name": name, "arguments": arguments})
        return self._responses.pop(0)


class StubLLM:
    """Returns LLMToolUseResponse objects matching the real gateway contract."""

    def __init__(self, scripted_responses: list[LLMToolUseResponse]):
        self._responses = scripted_responses
        self.calls: list[dict] = []

    async def chat_with_tools(self, **kwargs) -> LLMToolUseResponse:
        self.calls.append(kwargs)
        return self._responses.pop(0)


async def test_mcp_agent_completes_with_final_answer():
    llm = StubLLM(scripted_responses=[
        # Iteration 1: LLM asks to search
        LLMToolUseResponse(
            text=None,
            tool_calls=[ToolCall(id="tc1", name="search_documents",
                                 arguments={"query": "RFP requirements"})],
            model="gpt-5", input_tokens=10, output_tokens=20,
        ),
        # Iteration 2: LLM emits final answer
        LLMToolUseResponse(
            text="Found requirements [chunk-1]",
            tool_calls=[],
            model="gpt-5", input_tokens=30, output_tokens=15,
        ),
    ])
    mcp = StubMCPClient(
        tools=[StubMCPTool("search_documents", "search", {"type": "object"})],
        tool_responses=['{"chunks": [{"chunk_id": "chunk-1", "snippet": "..."}]}'],
    )

    cls = NodeRegistry.get("MCPAgent")
    node = cls(
        node_id="research",
        raw_config={"objective": "Find the requirements", "max_iterations": 5},
        services={"llm": llm, "mcp_client": mcp},
    )
    result = await node.run(
        state={"session_id": "sess-1"},
        resolved_config=node.config.model_dump(),
    )

    assert result["completed"] is True
    assert "Found requirements" in result["answer"]
    assert result["iterations_used"] == 2
    assert len(result["tool_calls"]) == 1
    # session_id was injected by the runtime, not by the LLM
    assert mcp.calls[0]["arguments"]["session_id"] == "sess-1"


async def test_mcp_agent_hits_max_iterations():
    """If the LLM never emits a final answer, we cap and return."""
    llm = StubLLM(scripted_responses=[
        # Three rounds of tool calls, never a final answer
        LLMToolUseResponse(
            text=None,
            tool_calls=[ToolCall(id="t1", name="search_documents", arguments={"query": "x"})],
            model="gpt-5", input_tokens=10, output_tokens=10,
        ),
        LLMToolUseResponse(
            text=None,
            tool_calls=[ToolCall(id="t2", name="search_documents", arguments={"query": "y"})],
            model="gpt-5", input_tokens=10, output_tokens=10,
        ),
        LLMToolUseResponse(
            text=None,
            tool_calls=[ToolCall(id="t3", name="search_documents", arguments={"query": "z"})],
            model="gpt-5", input_tokens=10, output_tokens=10,
        ),
    ])
    mcp = StubMCPClient(
        tools=[StubMCPTool("search_documents", "search", {"type": "object"})],
        tool_responses=['{"chunks":[]}', '{"chunks":[]}', '{"chunks":[]}'],
    )

    cls = NodeRegistry.get("MCPAgent")
    node = cls(
        node_id="research",
        raw_config={"objective": "Find it", "max_iterations": 3},
        services={"llm": llm, "mcp_client": mcp},
    )
    result = await node.run(state={"session_id": "s"}, resolved_config=node.config.model_dump())

    assert result["completed"] is False
    assert result["iterations_used"] == 3
    assert len(result["tool_calls"]) == 3


async def test_mcp_agent_injects_session_id_into_every_call():
    """LLM cannot override session_id; the runtime always sets it from state."""
    llm = StubLLM(scripted_responses=[
        LLMToolUseResponse(
            text=None,
            tool_calls=[ToolCall(
                id="t1", name="search_documents",
                # LLM tries to spoof a different session_id
                arguments={"query": "x", "session_id": "ATTACKER_SESSION"},
            )],
            model="gpt-5", input_tokens=10, output_tokens=10,
        ),
        LLMToolUseResponse(
            text="done", tool_calls=[],
            model="gpt-5", input_tokens=15, output_tokens=5,
        ),
    ])
    mcp = StubMCPClient(
        tools=[StubMCPTool("search_documents", "search", {"type": "object"})],
        tool_responses=['{"chunks":[]}'],
    )
    cls = NodeRegistry.get("MCPAgent")
    node = cls(
        node_id="r", raw_config={"objective": "go"}, services={"llm": llm, "mcp_client": mcp},
    )
    await node.run(state={"session_id": "REAL_SESSION"}, resolved_config=node.config.model_dump())

    # The runtime stomped the LLM's spoofed session_id
    assert mcp.calls[0]["arguments"]["session_id"] == "REAL_SESSION"