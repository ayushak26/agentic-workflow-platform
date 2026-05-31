import json
from app.evaluation.judge import LLMJudge, _JudgeVerdict
from app.evaluation.models import CriterionScore


class StubJudgeLLM:
    """Returns a fixed verdict regardless of input — lets us test the judge's
    parsing and shaping without a real model call."""
    def __init__(self, score: int, reasoning: str = "stub reason"):
        self._json = json.dumps({"score": score, "reasoning": reasoning})
        self.calls = []

    async def complete_structured(self, *, model, system, user,
                                  response_model, temperature=0.0, **_):
        self.calls.append({"model": model, "criterion_in_system": system[:40]})
        return response_model.model_validate_json(self._json)


async def test_judge_scores_one_criterion():
    judge = LLMJudge(StubJudgeLLM(score=4))
    result = await judge.score_one(
        "faithfulness",
        question="q", answer="a", context="[1] c", reference="r",
    )
    assert isinstance(result, CriterionScore)
    assert result.criterion == "faithfulness"
    assert result.score == 4
    assert result.reasoning == "stub reason"


async def test_judge_scores_all_four_criteria():
    llm = StubJudgeLLM(score=5)
    judge = LLMJudge(llm)
    scores = await judge.score_all(
        question="q", answer="a", context="[1] c", reference="r",
    )
    assert len(scores) == 4
    assert {s.criterion for s in scores} == {
        "faithfulness", "relevance", "completeness", "citation_accuracy"
    }
    # One LLM call per criterion — no batching.
    assert len(llm.calls) == 4


async def test_score_out_of_range_rejected():
    import pytest
    from pydantic import ValidationError
    judge = LLMJudge(StubJudgeLLM(score=7))   # invalid: > 5
    with pytest.raises(ValidationError):
        await judge.score_one(
            "relevance", question="q", answer="a", context="c", reference="r",
        )