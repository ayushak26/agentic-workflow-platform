"""LLM-as-a-Judge.

Core design decisions (all defensible in interview):

- ONE `complete_structured` call PER CRITERION, not one call scoring all four.
  Scoring everything in a single call causes cross-criterion anchoring: the
  model decides the answer is "good" and drags every score toward the same
  number. Isolating each criterion in its own call forces an independent
  judgment and gives cleaner, less-correlated scores.

- `temperature=0.0`. A judge should be as deterministic as the provider allows.
  We are measuring the output, not being creative about it.

- Structured output (`complete_structured` -> CriterionScore), never free-text
  JSON parsing. The gateway uses the provider's native structured mode, so we
  can't get a malformed score back.

- JUDGE_PROMPT_VERSION is bumped whenever a rubric changes. Two scorecards are
  only comparable if this version matches, which is exactly why it's persisted
  and indexed alongside judge_model.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from app.evaluation.models import CRITERIA, CriterionScore

# Bump this string whenever any rubric text below changes.
JUDGE_PROMPT_VERSION = "v1"

_JUDGE_SYSTEM = (
    "You are a strict, fair evaluation judge for a retrieval-augmented "
    "generation system. You score ONE criterion at a time on an integer scale "
    "of 1 to 5, where 1 is very poor and 5 is excellent. You must always give "
    "a concrete reason for the score, citing specifics from the answer and "
    "sources. Do not be generous: reserve 5 for answers that are clearly "
    "excellent on this criterion."
)

# Per-criterion rubric. Each is injected into the user prompt for that criterion.
_RUBRICS: dict[str, str] = {
    "faithfulness": (
        "FAITHFULNESS: Is every factual claim in the answer supported by the "
        "SOURCES? Penalize any claim, number, or name that does not appear in "
        "or follow from the sources (a hallucination). An answer can be fluent "
        "and still score 1 if it invents facts. A 5 means every claim is "
        "traceable to the sources."
    ),
    "relevance": (
        "RELEVANCE: Does the answer actually address the QUESTION asked? "
        "Penalize off-topic content, padding, or answering a different question. "
        "A 5 means the answer is tightly on-topic with no filler."
    ),
    "completeness": (
        "COMPLETENESS: Does the answer cover what the question asks for, given "
        "what the sources support? Penalize partial answers that omit "
        "information available in the sources. Do NOT penalize for omitting "
        "information that is not in the sources. A 5 means nothing supported and "
        "relevant was left out."
    ),
    "citation_accuracy": (
        "CITATION ACCURACY: Are the [N] citation labels in the answer correct "
        "and pointing at sources that actually support the cited claim? Penalize "
        "missing citations on factual claims, and citations that point to the "
        "wrong source. A 5 means every factual claim is cited and every citation "
        "is correct."
    ),
}


class _JudgeVerdict(BaseModel):
    """Provider-facing schema for one criterion.

    The criterion label is runtime-owned, so the model only returns the score
    and reasoning. This prevents the model from mislabelling a valid verdict.
    """

    score: int = Field(ge=1, le=5)
    reasoning: str


class LLMJudge:
    """Scores an answer on the four RAG criteria via the LLM gateway.

    Constructed as LLMJudge(llm_gateway, model=...). The gateway is any
    LLMGateway (typically the RegistryLLMGateway from app.llm).
    """

    def __init__(self, llm, *, model: str = "claude-sonnet-4-5") -> None:
        """Initialize the LLMJudge.

        Args:
            llm: The llm.
            model (str): Model name (optional, default 'claude-sonnet-4-5').
        """
        self.llm = llm
        self.model = model

    async def _score_one(
        self,
        *,
        criterion: str,
        question: str,
        answer: str,
        context: str,
        reference: str,
    ) -> CriterionScore:
        """Score the one.

        Args:
            criterion (str): The criterion.
            question (str): Question text.
            answer (str): The answer.
            context (str): The context.
            reference (str): The reference.

        Returns:
            CriterionScore: The one.
        """
        rubric = _RUBRICS[criterion]
        ref_block = (
            f"\n\nREFERENCE (an ideal answer, for your guidance only — the "
            f"answer under test need not match it word-for-word):\n{reference}"
            if reference
            else ""
        )
        user = (
            f"{rubric}\n\n"
            f"QUESTION:\n{question}\n\n"
            f"SOURCES:\n{context}\n\n"
            f"ANSWER UNDER TEST:\n{answer}"
            f"{ref_block}\n\n"
            f"Score ONLY the criterion described above."
        )
        verdict = await self.llm.complete_structured(
            model=self.model,
            system=_JUDGE_SYSTEM,
            user=user,
            response_model=_JudgeVerdict,
            temperature=0.0,
            max_tokens=512,
        )
        return CriterionScore(
            criterion=criterion,
            score=verdict.score,
            reasoning=verdict.reasoning,
        )

    async def score_one(
        self,
        criterion: str,
        *,
        question: str,
        answer: str,
        context: str,
        reference: str = "",
    ) -> CriterionScore:
        """Public single-criterion API used by focused evaluation runs."""

        if criterion not in CRITERIA:
            raise ValueError(
                f"unknown criterion {criterion!r}; expected one of {CRITERIA}"
            )
        return await self._score_one(
            criterion=criterion,
            question=question,
            answer=answer,
            context=context,
            reference=reference,
        )

    async def score_all(
        self,
        *,
        question: str,
        answer: str,
        context: str,
        reference: str = "",
    ) -> list[CriterionScore]:
        """Score one answer on all four criteria, one independent call each.

        Runs the four calls concurrently — they don't depend on each other, and
        this is exactly the FastAPI-async / parallel-branch story: independent
        awaitables gathered together.
        """
        tasks = [
            self._score_one(
                criterion=c,
                question=question,
                answer=answer,
                context=context,
                reference=reference,
            )
            for c in CRITERIA
        ]
        return list(await asyncio.gather(*tasks))