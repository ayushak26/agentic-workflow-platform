"""HumanInLoopAgent: pause and wait for user decision.

LangGraph's interrupt() raises a GraphInterrupt that the runtime catches.
State is checkpointed at the pause. Resuming with Command(resume=value)
returns `value` from inside the interrupt() call as if it had returned
normally."""
from __future__ import annotations

from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class HITLConfig(BaseModel):
    question: str                                  # shown to the human, templated
    context_fields: list[str] = Field(default_factory=list)
    # paths into state, e.g. ["rfp_intel.parsed.requirements"]
    allowed_actions: list[Literal["approve", "reject", "edit"]] = Field(
        default_factory=lambda: ["approve", "reject", "edit"]
    )


class HITLInput(BaseModel):
    pass


class HITLOutput(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    reason: str | None = None                      # set on reject
    edited_content: dict[str, Any] | None = None   # set on edit


class HITLInterruptPayload(BaseModel):
    """What the Cockpit sees when this node pauses."""
    node_id: str
    question: str
    context: dict[str, Any]
    allowed_actions: list[str]


@NodeRegistry.register
class HumanInLoopAgent(NodeType):
    type_name = "HumanInLoopAgent"
    description = "Pause for human approval, rejection, or edit."
    input_schema = HITLInput
    output_schema = HITLOutput
    config_schema = HITLConfig

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = HITLConfig(**resolved_config)

        # Collect requested context for the Cockpit display
        context = {}
        for path in cfg.context_fields:
            try:
                context[path] = _resolve_path(path, state)
            except KeyError:
                context[path] = None

        payload = HITLInterruptPayload(
            node_id=self.node_id,
            question=cfg.question,
            context=context,
            allowed_actions=cfg.allowed_actions,
        ).model_dump()

        # A restart-safe resume recompiles the graph and replays completed
        # nodes from Mongo. When that replay reaches the paused gate, the API-
        # validated decision is injected here. Normal in-process resumes still
        # use LangGraph's Command(resume=...) path.
        durable_decisions = self.services.get("hitl_resume_decisions") or {}
        if self.node_id in durable_decisions:
            user_decision = durable_decisions[self.node_id]
        else:
            # PAUSE — execution suspends here. The caller sees __interrupt__.
            user_decision = interrupt(payload)

        # Validate the decision shape
        decision = user_decision.get("decision")
        if decision not in cfg.allowed_actions:
            raise ValueError(
                f"HITL node {self.node_id} got disallowed decision: {decision!r}"
            )

        return {
            "decision": decision,
            "reason": user_decision.get("reason"),
            "edited_content": user_decision.get("edited_content"),
        }


def _resolve_path(path: str, state: dict) -> Any:
    parts = path.split(".")
    node_outputs = state.get("node_outputs", {})
    cursor: Any = node_outputs if parts[0] in node_outputs else state
    for p in parts:
        if isinstance(cursor, dict):
            cursor = cursor[p]
        else:
            cursor = getattr(cursor, p)
    return cursor
