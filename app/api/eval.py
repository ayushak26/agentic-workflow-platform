"""Eval Lab endpoints. Back the Phase 9 Eval Lab shell."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.evaluation import LLMJudge, load_golden_set, run_eval
from app.evaluation.golden_set import GoldenExample
from app.evaluation.judge import JUDGE_PROMPT_VERSION
from app.evaluation.workflow_golden import (
    load_workflow_golden_set,
    recommend_model,
    run_golden_set_with_model,
)
from app.security.dependencies import CurrentUser, require_permission
from app.security.guardrails import GuardrailViolation, check_workflow_inputs

router = APIRouter(prefix="/api/eval", tags=["eval"])

GOLDEN_DIR = Path("eval/golden_set")
REFERENCE_PDF = Path("samples/FARMLOOPS_proposal.pdf")

class RunEvalRequest(BaseModel):
    golden_set: str = "document_qa"   # filename stem under eval/golden_set/
    judge_model: str = "claude-sonnet-4-5"

class WorkflowCompareRequest(BaseModel):
    golden_set: str = "verder_customer_triage"   # filename stem under eval/golden_set/
    models: list[str] = ["claude-sonnet-4-5", "claude-haiku-4-5"]

class ScoreOutputRequest(BaseModel):
    answer: str
    sources: str = ""
    question: str = ""
    reference: str = ""          # optional ideal answer; used in-memory, never stored
    judge_model: str = "gpt-5"

def _load_reference() -> str:
    """Extract text from the stored reference proposal (gitignored, private).
    Reuses the ingestion PDF extractor so behavior matches how we read RFPs."""
    if not REFERENCE_PDF.exists():
        return ""
    from app.ingestion.extractor import PdfExtractor
    return PdfExtractor().extract(REFERENCE_PDF).full_text

@router.post("/score-output")
async def score_output(
    req: ScoreOutputRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("eval:run")),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        guarded = check_workflow_inputs(req.model_dump()).value
        req = ScoreOutputRequest.model_validate(guarded)
    except GuardrailViolation as exc:
        raise HTTPException(422, str(exc)) from exc
    reference = req.reference or _load_reference()
    llm = services["llm"]
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="eval:score-output",
            session_id=user.session_id or user.username,
            node_id="judge",
            ledger=services.get("cost_ledger"),
            workflow_name="Eval Lab · score output",
        )
    judge = LLMJudge(llm, model=req.judge_model)
    scores = await judge.score_all(
        question=req.question or "Evaluate this proposal against its sources.",
        answer=req.answer,
        context=req.sources,
        reference=reference,
    )
    return {
        "scores": [s.model_dump() for s in scores],
        "judge_model": req.judge_model,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
    }

@router.get("/golden-set")
async def list_golden_set(
    name: str = "document_qa",
    _user: CurrentUser = Depends(require_permission("eval:run")),
) -> dict[str, Any]:
    path = GOLDEN_DIR / f"{name}.jsonl"
    if not path.exists():
        raise HTTPException(404, f"Golden set not found: {name}")
    examples = load_golden_set(path)
    return {"name": name, "n": len(examples),
            "examples": [e.model_dump() for e in examples]}


@router.post("/run")
async def run(
    req: RunEvalRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("eval:run")),
) -> dict[str, Any]:
    path = GOLDEN_DIR / f"{req.golden_set}.jsonl"
    if not path.exists():
        raise HTTPException(404, f"Golden set not found: {req.golden_set}")
    examples = load_golden_set(path)

    services = request.app.state.services
    llm = services["llm"]
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id=f"eval:{req.golden_set}",
            session_id=user.session_id or user.username,
            node_id="judge",
            ledger=services.get("cost_ledger"),
            workflow_name=f"Eval Lab · {req.golden_set}",
        )
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
async def history(
    request: Request,
    limit: int = 20,
    _user: CurrentUser = Depends(require_permission("eval:run")),
) -> dict[str, Any]:
    mongo = request.app.state.services.get("mongo")
    if mongo is None:
        return {"scorecards": []}
    cards = await mongo.list_scorecards(limit=limit)
    return {"scorecards": cards}


@router.get("/workflow-golden-set")
async def list_workflow_golden_set(
    name: str = "verder_customer_triage",
    _user: CurrentUser = Depends(require_permission("eval:run")),
) -> dict[str, Any]:
    path = GOLDEN_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(404, f"Golden set not found: {name}")
    cases = load_workflow_golden_set(path)
    return {"name": name, "n": len(cases), "cases": [c.model_dump() for c in cases]}


@router.post("/workflow-compare")
async def workflow_compare(
    req: WorkflowCompareRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("eval:run")),
) -> dict[str, Any]:
    """Run the same golden set of real customer messages through the flagship
    triage workflow once per candidate model, and compare which one actually
    reaches the right business outcome — not a judged quality score, since
    there's a deterministic right answer (intent/complexity/route/review) to
    check against here."""
    path = GOLDEN_DIR / f"{req.golden_set}.json"
    if not path.exists():
        raise HTTPException(404, f"Golden set not found: {req.golden_set}")
    cases = load_workflow_golden_set(path)

    services = request.app.state.services
    run_id_prefix = f"eval:workflow-compare:{user.session_id or user.username}"
    comparisons = [
        await run_golden_set_with_model(
            cases, model=model, services=services, run_id_prefix=run_id_prefix,
        )
        for model in req.models
    ]

    return {
        "golden_set": req.golden_set,
        "comparisons": [c.model_dump() for c in comparisons],
        "recommendation": recommend_model(comparisons),
    }
