"""Workflow node for independent two-provider Horizon evaluation."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.horizon_evaluator import (
    HorizonEvaluationReport,
    evaluate_horizon_proposal,
)
from app.proposal_graph.state import proposal_graph_from_state


class HorizonEvaluationInput(BaseModel):
    pass


class HorizonEvaluationConfig(BaseModel):
    proposal_text: str
    generator_model: str | None = None
    evaluator_models: list[str] = Field(
        default_factory=lambda: ["claude-sonnet-4-5", "gpt-5"]
    )
    criterion_threshold: float = 3.0
    total_threshold: float = 10.0


@NodeRegistry.register
class HorizonEvaluationAgent(NodeType):
    type_name = "HorizonEvaluationAgent"
    description = (
        "Score Excellence, Impact, and Implementation with independent "
        "Anthropic and OpenAI evaluators plus deterministic evidence gates."
    )
    input_schema = HorizonEvaluationInput
    config_schema = HorizonEvaluationConfig
    output_schema = HorizonEvaluationReport

    async def run(self, state: dict, resolved_config: dict) -> dict:
        cfg = HorizonEvaluationConfig(**resolved_config)
        llm = self.services.get("llm")
        if llm is None:
            raise RuntimeError("HorizonEvaluationAgent requires the llm service")
        report = await evaluate_horizon_proposal(
            llm,
            proposal_text=cfg.proposal_text,
            graph=proposal_graph_from_state(state),
            generator_model=cfg.generator_model,
            evaluator_models=cfg.evaluator_models,
            criterion_threshold=cfg.criterion_threshold,
            total_threshold=cfg.total_threshold,
        )
        return report.model_dump()
