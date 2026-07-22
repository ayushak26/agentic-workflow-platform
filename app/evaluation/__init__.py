"""Evaluation package: LLM-as-a-Judge harness.

Public surface consumed by app/api/eval.py:
    from app.evaluation import LLMJudge, load_golden_set, run_eval
    from app.evaluation.golden_set import GoldenExample
    from app.evaluation.judge import JUDGE_PROMPT_VERSION
"""
from app.evaluation.golden_set import GoldenExample, load_golden_set
from app.evaluation.judge import JUDGE_PROMPT_VERSION, LLMJudge
from app.evaluation.models import (
    CRITERIA,
    CriterionScore,
    ExampleResult,
    Scorecard,
)
from app.evaluation.runner import ProduceAnswer, run_eval

__all__ = [
    "LLMJudge",
    "JUDGE_PROMPT_VERSION",
    "GoldenExample",
    "load_golden_set",
    "run_eval",
    "ProduceAnswer",
    "Scorecard",
    "ExampleResult",
    "CriterionScore",
    "CRITERIA",
]