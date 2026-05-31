"""Evaluation harness: LLM-as-a-Judge over a golden set."""
from .runner import run_eval
from .judge import LLMJudge
from .golden_set import GoldenExample, load_golden_set
from .models import Scorecard, ExampleResult, CriterionScore

__all__ = [
    "run_eval", "LLMJudge", "GoldenExample", "load_golden_set",
    "Scorecard", "ExampleResult", "CriterionScore",
]