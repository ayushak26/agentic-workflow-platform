"""Evaluation data models.

Design decisions worth defending in an interview:

- Scores are DISCRETE integers 1-5, not floats 0-1. LLM judges classify far
  more reliably than they estimate a continuous number. "Is this a 4 or a 5?"
  is a decision a model can make consistently; "is this 0.82 or 0.79?" is noise.

- Every CriterionScore carries a required `reasoning` string. A score with no
  reasoning is not auditable, and an unauditable judge is worthless. Forcing the
  model to justify the number also improves the number (chain-of-thought effect).

- Scorecard pins `judge_model` and `judge_prompt_version`. A score is only
  comparable to another score produced by the SAME judge under the SAME rubric.
  These two fields are why the Mongo compound index on
  (workflow_name, judge_model, judge_prompt_version) exists — drop them and
  cross-run comparison becomes apples-to-oranges.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

# The four RAG-quality criteria. Order is stable so scorecards line up.
CRITERIA: tuple[str, ...] = (
    "faithfulness",
    "relevance",
    "completeness",
    "citation_accuracy",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CriterionScore(BaseModel):
    """One judged criterion for one answer."""
    criterion: str
    score: int = Field(ge=1, le=5)
    reasoning: str


class ExampleResult(BaseModel):
    """All criterion scores for a single golden example."""
    example_id: str
    question: str
    answer: str
    scores: list[CriterionScore]

    @property
    def mean(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.score for s in self.scores) / len(self.scores)


class Scorecard(BaseModel):
    """Aggregate result of evaluating one workflow over one golden set.

    `.model_dump()` of this is what gets persisted to Mongo and returned to the
    Eval Lab UI, so field names here are a stable API surface.
    """
    workflow_name: str
    judge_model: str
    judge_prompt_version: str
    created_at: str = Field(default_factory=_now_iso)

    n_examples: int
    results: list[ExampleResult]

    # Per-criterion averages across all examples, e.g. {"faithfulness": 4.2, ...}
    criterion_means: dict[str, float]
    overall_mean: float

    @property
    def per_criterion_mean(self) -> dict[str, float]:
        """Backward-compatible read alias for earlier API/UI consumers."""

        return self.criterion_means