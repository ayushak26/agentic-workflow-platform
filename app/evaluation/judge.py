"""LLM-as-a-Judge. One criterion per call, discrete 1-5, reasoning required.

This is the 'LLM-as-a-Judge' pattern by hand. RAGAS / MLflow / LangChain
Criteria Evaluation are the productized versions of the same idea — named in
docs/evaluation.md so I can speak to them, but not added as dependencies
(no new vendor in the locked stack).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import Criterion, CriterionScore

JUDGE_PROMPT_VERSION = "v1"   # bump when the rubric changes; travels with scores

# One rubric per criterion. Each is a complete task + criterion + scale, the
# three things Huyen says a judge prompt must spell out.
_RUBRICS: dict[Criterion, str] = {
    "faithfulness": (
        "Score how FAITHFUL the answer is to the provided context. "
        "Every factual claim in the answer must be supported by the context. "
        "5 = every claim is grounded in the context; "
        "3 = mostly grounded, one unsupported claim; "
        "1 = answer invents facts not in the context."
    ),
    "relevance": (
        "Score how RELEVANT the answer is to the question, using the reference "
        "answer as the standard for what a good answer covers. "
        "5 = directly and fully addresses the question; "
        "3 = partially addresses it; "
        "1 = does not address the question."
    ),
    "completeness": (
        "Score how COMPLETE the answer is versus the reference answer. "
        "5 = covers all key points in the reference; "
        "3 = covers some; 1 = misses the main points."
    ),
    "citation_accuracy": (
        "Score CITATION ACCURACY. The answer should cite sources with [N] labels "
        "that correspond to real provided sources. "
        "5 = every claim cited and every citation valid; "
        "3 = some claims uncited or one invalid citation; "
        "1 = no citations or fabricated citations."
    ),
}


class _JudgeVerdict(BaseModel):
    """The structured shape we force the judge to return."""
    score: int = Field(ge=1, le=5)
    reasoning: str


class LLMJudge:
    def __init__(self, llm: Any, model: str = "claude-sonnet-4-5"):
        self.llm = llm
        self.model = model   # pinned — logged with every score

    async def score_one(
        self,
        criterion: Criterion,
        *,
        question: str,
        answer: str,
        context: str,
        reference: str,
    ) -> CriterionScore:
        rubric = _RUBRICS[criterion]
        system = (
            "You are a strict evaluation judge. Score the answer on the single "
            "criterion below using a discrete 1-5 scale. Return your score and a "
            "one-sentence reason. Be conservative: when unsure, score lower.\n\n"
            f"CRITERION: {rubric}"
        )
        user = (
            f"QUESTION:\n{question}\n\n"
            f"REFERENCE ANSWER:\n{reference}\n\n"
            f"PROVIDED CONTEXT:\n{context}\n\n"
            f"ANSWER TO SCORE:\n{answer}"
        )
        verdict: _JudgeVerdict = await self.llm.complete_structured(
            model=self.model,
            system=system,
            user=user,
            response_model=_JudgeVerdict,
            temperature=0.0,   # judge should be as deterministic as possible
        )
        return CriterionScore(
            criterion=criterion,
            score=verdict.score,
            reasoning=verdict.reasoning,
        )

    async def score_all(
        self, *, question: str, answer: str, context: str, reference: str
    ) -> list[CriterionScore]:
        # One call per criterion — independent scores, no cross-criterion anchoring.
        out: list[CriterionScore] = []
        for criterion in ("faithfulness", "relevance", "completeness", "citation_accuracy"):
            out.append(
                await self.score_one(
                    criterion, question=question, answer=answer,
                    context=context, reference=reference,
                )
            )
        return out