"""Run a golden set through an answer-producing function and score it.

The runner does NOT know about LangGraph. It takes a `produce_answer` callable
(GoldenExample -> (answer, context_used)). That's what makes the harness able to
evaluate ANY workflow, a raw RAG path, or prompt-version A vs B — you just pass a
different producer. The judge and aggregation stay identical.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Awaitable, Callable

from .golden_set import GoldenExample
from .judge import JUDGE_PROMPT_VERSION, LLMJudge
from .models import ExampleResult, Scorecard

# producer: example -> (generated_answer, context_string_the_answer_saw)
ProduceAnswer = Callable[[GoldenExample], Awaitable[tuple[str, str]]]


async def run_eval(
    *,
    workflow_name: str,
    examples: list[GoldenExample],
    judge: LLMJudge,
    produce_answer: ProduceAnswer,
) -> Scorecard:
    results: list[ExampleResult] = []

    for ex in examples:
        answer, context_used = await produce_answer(ex)
        scores = await judge.score_all(
            question=ex.question,
            answer=answer,
            context=context_used or ex.context,   # fall back to golden context
            reference=ex.reference,
        )
        results.append(ExampleResult(
            example_id=ex.id,
            question=ex.question,
            generated_answer=answer,
            scores=scores,
        ))

    # Aggregate: mean per criterion across all examples, plus overall mean.
    by_criterion: dict[str, list[int]] = defaultdict(list)
    for r in results:
        for s in r.scores:
            by_criterion[s.criterion].append(s.score)

    per_criterion_mean = {
        crit: sum(vals) / len(vals) for crit, vals in by_criterion.items()
    }
    overall_mean = (
        sum(per_criterion_mean.values()) / len(per_criterion_mean)
        if per_criterion_mean else 0.0
    )

    return Scorecard(
        workflow_name=workflow_name,
        judge_model=judge.model,
        judge_prompt_version=JUDGE_PROMPT_VERSION,
        n_examples=len(examples),
        per_criterion_mean=per_criterion_mean,
        overall_mean=overall_mean,
        results=results,
    )