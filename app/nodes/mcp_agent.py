"""MCPAgent: an LLM-driven agent loop calling MCP tools.

This is the first node with a real agent loop. Each iteration:
  1. LLM sees the current message history + available tools
  2. LLM either calls a tool (which we execute) or emits a final answer
  3. Tool results get appended to the message history
Loop terminates on final answer or hitting max_iterations."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)


class MCPAgentConfig(BaseModel):
    """Pydantic model defining the MCPAgentConfig shape.

    Attributes:
        model (str).
        objective (str).
        system_prompt (str).
        max_iterations (int).
        temperature (float).
        allowed_tools (list[str] | None).
    """
    model: str = "claude-sonnet-4-5"
    objective: str                                  # templated user goal
    system_prompt: str = (
        "You are a research agent. Use the available tools to gather "
        "evidence and answer the objective. Cite chunk_ids in your final "
        "answer like [chunk_id_here]. When you are done, respond with the "
        "final answer — no more tool calls."
    )
    max_iterations: int = 5
    temperature: float = 0.2
    #: Restricts which of the connected server's tools this step may see and
    #: call. `None` (the default) is unrestricted — every discovered tool is
    #: offered to the model, which is why preflight warns when this is unset
    #: and no human review precedes the node (see logic_preflight.py).
    allowed_tools: list[str] | None = None


class MCPAgentInput(BaseModel):
    """Pydantic model defining the MCPAgentInput shape."""
    pass


class ToolCallRecord(BaseModel):
    """Pydantic model defining the ToolCallRecord shape.

    Attributes:
        iteration (int).
        tool (str).
        arguments (dict).
        result_preview (str).
    """
    iteration: int
    tool: str
    arguments: dict
    result_preview: str                             # first 240 chars


class MCPAgentOutput(BaseModel):
    """Pydantic model defining the MCPAgentOutput shape.

    Attributes:
        answer (str).
        tool_calls (list[ToolCallRecord]).
        iterations_used (int).
        completed (bool).
    """
    answer: str
    tool_calls: list[ToolCallRecord]
    iterations_used: int
    completed: bool                                 # False if hit max_iterations


@NodeRegistry.register
class MCPAgent(NodeType):
    """Workflow node type implementing the MCPAgent capability."""
    type_name = "MCPAgent"
    description = "LLM-driven agent loop using MCP tools."
    input_schema = MCPAgentInput
    output_schema = MCPAgentOutput
    config_schema = MCPAgentConfig

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"llm", "cost_ledger", "mcp_client"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = MCPAgentConfig(**resolved_config)
        llm = self.services["llm"]
        mcp = self.services["mcp_client"]
        session_id = state["session_id"]

        # 1. Discover tools and convert to the LLM's tool-definition format
        mcp_tools = await mcp.list_tools()
        if cfg.allowed_tools is not None:
            # Never let the model see a tool outside its declared allowlist —
            # narrowing here (not just at call time) keeps an out-of-scope
            # tool from ever being offered as a choice in the first place.
            mcp_tools = [t for t in mcp_tools if t.name in cfg.allowed_tools]
        llm_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in mcp_tools
        ]

        messages: list[dict] = [
            {"role": "user", "content": cfg.objective}
        ]
        tool_call_log: list[ToolCallRecord] = []

        for iteration in range(cfg.max_iterations):
            response = await llm.chat_with_tools(
                model=cfg.model,
                system=cfg.system_prompt,
                messages=messages,
                tools=llm_tools,
                temperature=cfg.temperature,
            )

            # Response shape: {"content": str|None, "tool_calls": [{"id", "name", "arguments"}]}
            if not response.tool_calls:
                return MCPAgentOutput(
                    answer=response.text or "",
                    tool_calls=tool_call_log,
                    iterations_used=iteration + 1,
                    completed=True,
                ).model_dump()


            # Execute each tool call; append results to the conversation
            assistant_turn = {
                "role": "assistant",
                "content": response.text or "",
                # Kimi K3 uses preserved thinking history. Keeping this field
                # provider-neutral is harmless for gateways that ignore it.
                "reasoning_content": response.reasoning_content,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_turn)

            for tc in response.tool_calls:
                if cfg.allowed_tools is not None and tc.name not in cfg.allowed_tools:
                    # Defense in depth: even if a model somehow names a tool it
                    # wasn't shown (e.g. from prior conversation context or a
                    # prompt injection), never execute it — tell the model and
                    # let the loop continue instead of crashing the run.
                    log.warning(
                        "mcp_agent.tool_not_allowed",
                        node_id=self.node_id,
                        tool=tc.name,
                    )
                    denial = f"Tool '{tc.name}' is not permitted for this step and was not called."
                    tool_call_log.append(ToolCallRecord(
                        iteration=iteration,
                        tool=tc.name,
                        arguments=dict(tc.arguments),
                        result_preview=denial,
                    ))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": denial,
                    })
                    continue

                args = dict(tc.arguments)
                args["session_id"] = session_id

                result = await mcp.call_tool(tc.name, args)
                tool_call_log.append(ToolCallRecord(
                    iteration=iteration,
                    tool=tc.name,
                    arguments=args,
                    result_preview=(result[:240] + "...") if len(result) > 240 else result,
                ))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # Loop exhausted without final answer
        log.warning("mcp_agent.max_iterations", node_id=self.node_id, iterations=cfg.max_iterations)
        return MCPAgentOutput(
            answer="Agent exhausted iterations without a final answer.",
            tool_calls=tool_call_log,
            iterations_used=cfg.max_iterations,
            completed=False,
        ).model_dump()
