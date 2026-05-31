"""Pydantic models for the evaluation harness.

Scoring is DISCRETE 1-5 (Huyen: judges classify more reliably than they
estimate continuous values; wider discrete ranges degrade further, so 1-5
is the sweet spot). Each CriterionScore carries the judge's reasoning — a
score with no rationale is untrustworthy (Huyen: 'do not trust any judge
if you can't see the model and the prompt').
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Criterion = Literal["faithfulness", "relevance", "completeness", "citation_accuracy"]


class CriterionScore(BaseModel):
    criterion: Criterion
    score: int = Field(ge=1, le=5)         # discrete 1-5
    reasoning: str                          # why this score — never optional


class ExampleResult(BaseModel):
    example_id: str
    question: str
    generated_answer: str
    scores: list[CriterionScore]

    def mean_score(self) -> float:
        return sum(s.score for s in self.scores) / len(self.scores)


class Scorecard(BaseModel):
    """Aggregate over a golden-set run. Pinned judge identity travels with the
    scorecard so two scorecards are only comparable when judge_model +
    judge_prompt_version match."""
    workflow_name: str
    judge_model: str
    judge_prompt_version: str
    n_examples: int
    per_criterion_mean: dict[str, float]   # criterion -> mean score across examples
    overall_mean: float
    results: list[ExampleResult]
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )