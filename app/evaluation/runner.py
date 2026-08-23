"""Evaluation runner.

The key decoupling: `run_eval` does NOT know how answers are produced. It takes
a `produce_answer` callable — (GoldenExample) -> (answer, context) — so the SAME
runner can score:
  - a raw LLM generation grounded in golden context (generation-only eval),
  - a full workflow run (end-to-end eval),
  - variant A vs variant B (A/B prompt or workflow comparison).

That's the "evaluate ANY workflow" property from the main interview story. The
runner is a scoring loop; what it scores is injected.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app.evaluation.golden_set import GoldenExample
from app.evaluation.judge import JUDGE_PROMPT_VERSION, LLMJudge
from app.evaluation.models import (
    CRITERIA,
    ExampleResult,
    Scorecard,
)

# (example) -> (answer_text, context_used_for_that_answer)
ProduceAnswer = Callable[[GoldenExample], Awaitable[tuple[str, str]]]


async def _eval_one(
    ex: GoldenExample,
    judge: LLMJudge,
    produce_answer: ProduceAnswer,
) -> ExampleResult:
    """Internal helper for the eval one step.

    Args:
        ex (GoldenExample): The ex.
        judge (LLMJudge): The judge.
        produce_answer (ProduceAnswer): The produce answer.

    Returns:
        ExampleResult: The one.
    """
    answer, context = await produce_answer(ex)
    scores = await judge.score_all(
        question=ex.question,
        answer=answer,
        context=context,
        reference=ex.reference,
    )
    return ExampleResult(
        example_id=ex.id,
        question=ex.question,
        answer=answer,
        scores=scores,
    )


async def run_eval(
    *,
    workflow_name: str,
    examples: list[GoldenExample],
    judge: LLMJudge,
    produce_answer: ProduceAnswer,
) -> Scorecard:
    """Run the whole golden set through produce_answer + judge, aggregate.

    Examples are evaluated concurrently. Each example internally fires four
    judge calls (also concurrent). For a tiny local golden set this is fine; if
    a set ever got large you'd add a semaphore to bound provider concurrency.
    """
    results: list[ExampleResult] = list(
        await asyncio.gather(
            *(_eval_one(ex, judge, produce_answer) for ex in examples)
        )
    )

    # Per-criterion means across all examples.
    criterion_means: dict[str, float] = {}
    for crit in CRITERIA:
        vals = [
            s.score
            for r in results
            for s in r.scores
            if s.criterion == crit
        ]
        criterion_means[crit] = round(sum(vals) / len(vals), 3) if vals else 0.0

    overall = (
        round(sum(criterion_means.values()) / len(criterion_means), 3)
        if criterion_means
        else 0.0
    )

    return Scorecard(
        workflow_name=workflow_name,
        judge_model=judge.model,
        judge_prompt_version=JUDGE_PROMPT_VERSION,
        n_examples=len(examples),
        results=results,
        criterion_means=criterion_means,
        overall_mean=overall,
    )