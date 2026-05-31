"""Eval Lab endpoints. Back the Phase 9 Eval Lab shell."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.evaluation import LLMJudge, load_golden_set, run_eval
from app.evaluation.golden_set import GoldenExample

router = APIRouter(prefix="/api/eval", tags=["eval"])

GOLDEN_DIR = Path("eval/golden_set")


class RunEvalRequest(BaseModel):
    golden_set: str = "document_qa"   # filename stem under eval/golden_set/
    judge_model: str = "claude-sonnet-4-5"


@router.get("/golden-set")
async def list_golden_set(name: str = "document_qa") -> dict[str, Any]:
    path = GOLDEN_DIR / f"{name}.jsonl"
    if not path.exists():
        raise HTTPException(404, f"Golden set not found: {name}")
    examples = load_golden_set(path)
    return {"name": name, "n": len(examples),
            "examples": [e.model_dump() for e in examples]}


@router.post("/run")
async def run(req: RunEvalRequest, request: Request) -> dict[str, Any]:
    path = GOLDEN_DIR / f"{req.golden_set}.jsonl"
    if not path.exists():
        raise HTTPException(404, f"Golden set not found: {req.golden_set}")
    examples = load_golden_set(path)

    services = request.app.state.services
    llm = services["llm"]
    judge = LLMJudge(llm, model=req.judge_model)

    # Producer: for the document_qa path, answer the question grounded ONLY in
    # the golden context. This isolates GENERATION quality from RETRIEVAL — we
    # feed known-good context so the score reflects the model's grounding, not
    # the retriever's recall. (Evaluating retrieval separately is a follow-up.)
    async def produce_answer(ex: GoldenExample) -> tuple[str, str]:
        resp = await llm.complete(
            model=req.judge_model,
            system=("Answer the question using ONLY the provided sources. "
                    "Cite every factual claim with its [N] label."),
            user=f"QUESTION: {ex.question}\n\nSOURCES:\n{ex.context}",
            temperature=0.2,
        )
        return resp.text, ex.context

    scorecard = await run_eval(
        workflow_name=req.golden_set,
        examples=examples,
        judge=judge,
        produce_answer=produce_answer,
    )

    # Persist to Mongo for history (best-effort; eval still returns if Mongo down).
    mongo = services.get("mongo")
    if mongo is not None:
        try:
            await mongo.save_scorecard(scorecard.model_dump())
        except Exception:
            pass

    return scorecard.model_dump()


@router.get("/history")
async def history(request: Request, limit: int = 20) -> dict[str, Any]:
    mongo = request.app.state.services.get("mongo")
    if mongo is None:
        return {"scorecards": []}
    cards = await mongo.list_scorecards(limit=limit)
    return {"scorecards": cards}