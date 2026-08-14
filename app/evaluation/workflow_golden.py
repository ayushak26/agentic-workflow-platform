"""Golden-case evaluation for a real end-to-end workflow (not a raw generation).

Different shape from `app.evaluation.golden_set`/`runner.py` on purpose: those
score RAG *generation quality* with an LLM judge over (question, context,
reference) triples. Here there is no "ideal answer" to grade against — the
thing being checked is whether the workflow reached the right *business
outcome* (which intent, which complexity tier, did it correctly ask a human
for help) for a known customer message. That is a deterministic comparison
against `assess_request`'s rule-derived decisions, not a judged score.

Each case's `expected` only names the decisions worth pinning for that case —
a case is free to leave a field unspecified when the point it demonstrates
doesn't depend on it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.observability.cost_ledger import CostLedger
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow

WORKFLOW = load_workflow("workflows/crm_aware_customer_triage.yaml")


class WorkflowGoldenCase(BaseModel):
    id: str
    label: str
    subject: str
    message: str
    sender_email: str
    expected: dict[str, Any]


def load_workflow_golden_set(path: str | Path) -> list[WorkflowGoldenCase]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Golden set not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [WorkflowGoldenCase.model_validate(case) for case in raw]


class FieldCheck(BaseModel):
    field: str
    expected: Any
    actual: Any
    passed: bool


class WorkflowCaseResult(BaseModel):
    case_id: str
    label: str
    model: str
    passed: bool
    checks: list[FieldCheck]
    cost_usd: float | None = None
    latency_ms: float | None = None
    error: str | None = None


class ModelComparisonResult(BaseModel):
    model: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    avg_cost_usd: float | None
    avg_latency_ms: float | None
    cases: list[WorkflowCaseResult]


class ModelOverrideGateway:
    """Forces every LLM call a run makes onto one candidate model, regardless
    of what the node/YAML asked for (including AUTO_MODEL routing).

    Used only here, to answer "how would this workflow perform if it ran on
    model X end to end" — a real question for a model-comparison eval, not a
    thing production traffic should ever do.
    """

    def __init__(self, inner: Any, model: str):
        self._inner = inner
        self._model = model

    def with_context(self, **kwargs: Any) -> "ModelOverrideGateway":
        return ModelOverrideGateway(self._inner.with_context(**kwargs), self._model)

    async def complete(self, *, model: str | None = None, **kwargs: Any):
        del model
        return await self._inner.complete(model=self._model, **kwargs)

    async def complete_structured(self, *, model: str | None = None, **kwargs: Any):
        del model
        return await self._inner.complete_structured(model=self._model, **kwargs)

    async def chat_with_tools(self, *, model: str | None = None, **kwargs: Any):
        del model
        return await self._inner.chat_with_tools(model=self._model, **kwargs)


def _actual_value(result: dict[str, Any], field: str) -> Any:
    if field == "status":
        return result.get("status")
    node_outputs = result.get("state", {}).get("node_outputs", {})
    if field == "route":
        return node_outputs.get("route_request", {}).get("route")
    decisions = node_outputs.get("assess_request", {}).get("decisions", {})
    return decisions.get(field)


async def run_workflow_golden_case(
    case: WorkflowGoldenCase,
    *,
    model: str,
    services: dict[str, Any],
    run_id: str,
) -> WorkflowCaseResult:
    run_services = {**services, "llm": ModelOverrideGateway(services["llm"], model)}
    started = time.monotonic()
    try:
        result = await run_workflow(
            WORKFLOW,
            inputs={
                "subject": case.subject,
                "message": case.message,
                "sender_email": case.sender_email,
            },
            services=run_services,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001 — a bad candidate model failing is a real eval outcome, not a bug
        return WorkflowCaseResult(
            case_id=case.id, label=case.label, model=model,
            passed=False, checks=[], error=str(exc),
        )
    elapsed_ms = (time.monotonic() - started) * 1000

    checks = []
    for field, expected in case.expected.items():
        actual = _actual_value(result, field)
        checks.append(FieldCheck(field=field, expected=expected, actual=actual, passed=actual == expected))

    cost_usd: float | None = None
    latency_ms: float | None = elapsed_ms
    cost_ledger = services.get("cost_ledger")
    if isinstance(cost_ledger, CostLedger):
        summary = cost_ledger.run_summary(run_id)
        cost_usd = summary["total_usd"]
        samples = [e["latency_ms"] for e in summary["by_node"] if e.get("latency_ms") is not None]
        if samples:
            latency_ms = sum(samples) / len(samples)

    return WorkflowCaseResult(
        case_id=case.id, label=case.label, model=model,
        passed=all(c.passed for c in checks), checks=checks,
        cost_usd=cost_usd, latency_ms=latency_ms,
    )


async def run_golden_set_with_model(
    cases: list[WorkflowGoldenCase],
    *,
    model: str,
    services: dict[str, Any],
    run_id_prefix: str,
) -> ModelComparisonResult:
    results = [
        await run_workflow_golden_case(
            case, model=model, services=services, run_id=f"{run_id_prefix}:{model}:{case.id}",
        )
        for case in cases
    ]
    passed = sum(1 for r in results if r.passed)
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    return ModelComparisonResult(
        model=model,
        total_cases=len(results),
        passed_cases=passed,
        pass_rate=round(passed / len(results), 3) if results else 0.0,
        avg_cost_usd=round(sum(costs) / len(costs), 6) if costs else None,
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
        cases=results,
    )


def recommend_model(comparisons: list[ModelComparisonResult]) -> dict[str, Any] | None:
    """Highest pass rate wins; ties broken by lower cost, then lower latency.
    Never recommends a model that failed every case."""
    candidates = [c for c in comparisons if c.pass_rate > 0]
    if not candidates:
        return None
    best = min(
        candidates,
        key=lambda c: (-c.pass_rate, c.avg_cost_usd if c.avg_cost_usd is not None else float("inf")),
    )
    return {
        "model": best.model,
        "reason": (
            f"{best.model} passed {best.passed_cases}/{best.total_cases} golden cases"
            + (f" at an average ${best.avg_cost_usd:.4f} per run" if best.avg_cost_usd is not None else "")
            + "."
        ),
    }
