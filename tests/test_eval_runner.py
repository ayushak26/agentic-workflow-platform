import json
from app.evaluation.runner import run_eval
from app.evaluation.judge import LLMJudge
from app.evaluation.golden_set import GoldenExample


class StubJudgeLLM:
    def __init__(self, score: int):
        self._json = json.dumps({"score": score, "reasoning": "stub"})

    async def complete_structured(self, *, model, system, user,
                                  response_model, temperature=0.0, **_):
        return response_model.model_validate_json(self._json)


async def test_runner_aggregates_scores_into_scorecard():
    examples = [
        GoldenExample(id="a", question="q1", context="[1] c1", reference="r1"),
        GoldenExample(id="b", question="q2", context="[1] c2", reference="r2"),
    ]
    judge = LLMJudge(StubJudgeLLM(score=4))

    async def produce(ex):
        return f"answer for {ex.id}", ex.context

    card = await run_eval(
        workflow_name="document_qa",
        examples=examples,
        judge=judge,
        produce_answer=produce,
    )

    assert card.n_examples == 2
    assert len(card.results) == 2
    # Every criterion scored 4, so every mean is 4.0.
    assert card.overall_mean == 4.0
    assert set(card.per_criterion_mean) == {
        "faithfulness", "relevance", "completeness", "citation_accuracy"
    }
    # The pinned judge identity must travel with the scorecard.
    assert card.judge_prompt_version == "v1"
    assert card.judge_model  # non-empty


async def test_runner_uses_produced_context_over_golden():
    """If the producer returns its own context, the judge sees that, not the
    golden context — this is how A/B and real-retrieval evals work."""
    seen = {}

    class CapturingJudge(LLMJudge):
        async def score_all(self, *, question, answer, context, reference):
            seen["context"] = context
            return await super().score_all(
                question=question, answer=answer, context=context, reference=reference,
            )

    judge = CapturingJudge(StubJudgeLLM(score=3))

    async def produce(ex):
        return "ans", "PRODUCER CONTEXT [1]"

    await run_eval(
        workflow_name="x",
        examples=[GoldenExample(id="a", question="q", context="GOLDEN [1]", reference="r")],
        judge=judge,
        produce_answer=produce,
    )
    assert seen["context"] == "PRODUCER CONTEXT [1]"