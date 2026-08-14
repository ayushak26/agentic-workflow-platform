"""Account-wide cost management — the standalone admin page, distinct from app/api/cost.py's
per-run/per-session views. Everything here is admin-gated (require_admin): pricing-table
overrides, private-infra cost allocation, prompt-cache savings, budget thresholds, and the
cross-run overview that ties them together (by model/provider/workflow/collection, with a
daily trend).

Mongo access mirrors app/observability/cost_ledger.py's own convention: the synchronous
pymongo `services["db"]` handle (not the async motor `services["mongo"]`/`services["audit_db"]`
used elsewhere) — same underlying database, and this module's queries are all fast local
lookups, consistent with how app/api/cost.py already calls CostLedger's sync methods from
inside async route handlers.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.llm.openrouter_catalog import get_default_cache
from app.observability.cost_ledger import (
    ANTHROPIC_CACHE_READ_MULTIPLIER,
    MODEL_PRICING,
    OPENAI_CACHE_READ_MULTIPLIER,
)
from app.security.dependencies import CurrentUser, require_admin

router = APIRouter(prefix="/api/cost-admin", tags=["cost-admin"])


def _db(request: Request):
    return getattr(request.app.state, "services", {}).get("db")


def _cost_ledger_entries(db, *, since: datetime | None = None) -> list[dict]:
    if db is None:
        return []
    query: dict[str, Any] = {}
    if since is not None:
        query["ts"] = {"$gte": since}
    return list(db["cost_ledger"].find(query, {"_id": 0}))


# ---------------------------------------------------------------------------
# Pricing administration
# ---------------------------------------------------------------------------


class PricingOverrideRequest(BaseModel):
    input_usd_per_1k: float = Field(ge=0)
    output_usd_per_1k: float = Field(ge=0)


@router.get("/pricing")
async def list_pricing(
    request: Request,
    openrouter_q: str | None = Query(default=None, max_length=200),
    openrouter_limit: int = Query(default=25, ge=1, le=200),
    user: CurrentUser = Depends(require_admin),
):
    """Direct/local catalog pricing (editable overrides layered on MODEL_PRICING defaults)
    plus a live, read-only slice of OpenRouter's own per-model pricing — OpenRouter's ~400-500
    models are priced by OpenRouter itself and reported per-call via usage.cost
    (LedgerEntry.cost_source == "provider_reported"), so there is nothing to override there;
    this is for visibility, not administration."""
    del user
    db = _db(request)
    overrides: dict[str, dict[str, float]] = {}
    if db is not None:
        overrides = {
            doc["model"]: doc
            for doc in db["pricing_overrides"].find({}, {"_id": 0})
        }

    direct = []
    for model, (default_in, default_out) in MODEL_PRICING.items():
        override = overrides.get(model)
        direct.append({
            "model": model,
            "input_usd_per_1k": override["input_usd_per_1k"] if override else default_in,
            "output_usd_per_1k": override["output_usd_per_1k"] if override else default_out,
            "default_input_usd_per_1k": default_in,
            "default_output_usd_per_1k": default_out,
            "source": "override" if override else "default",
        })
    direct.sort(key=lambda item: item["model"])

    openrouter_models = await get_default_cache().search(openrouter_q, limit=openrouter_limit)
    openrouter = [
        {
            "model": model.id,
            "display_name": model.display_name,
            "input_usd_per_million": model.input_usd_per_million,
            "output_usd_per_million": model.output_usd_per_million,
        }
        for model in openrouter_models
    ]

    return {"direct": direct, "openrouter": openrouter}


@router.put("/pricing/{model}")
async def set_pricing_override(
    model: str,
    body: PricingOverrideRequest,
    request: Request,
    user: CurrentUser = Depends(require_admin),
):
    db = _db(request)
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    if model not in MODEL_PRICING:
        raise HTTPException(
            status_code=404,
            detail=f"{model!r} is not in the approved model catalog — overrides only apply "
            "to direct/local models already known to Eurskem.",
        )
    db["pricing_overrides"].update_one(
        {"model": model},
        {
            "$set": {
                "model": model,
                "input_usd_per_1k": body.input_usd_per_1k,
                "output_usd_per_1k": body.output_usd_per_1k,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": user.username,
            }
        },
        upsert=True,
    )
    return {"model": model, "status": "overridden"}


@router.delete("/pricing/{model}")
async def clear_pricing_override(
    model: str,
    request: Request,
    user: CurrentUser = Depends(require_admin),
):
    del user
    db = _db(request)
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    db["pricing_overrides"].delete_one({"model": model})
    return {"model": model, "status": "reverted_to_default"}


# ---------------------------------------------------------------------------
# Private-infra cost allocation — local models are $0 API-metered but have a real GPU/
# infra cost. This is an operator-configured ESTIMATE, applied only at report time; it never
# rewrites the real (accurate, $0) LedgerEntry.cost_usd figures.
# ---------------------------------------------------------------------------

_LOCAL_MODELS = tuple(model for model in MODEL_PRICING if model.startswith("local-"))


class InfraAllocationRequest(BaseModel):
    allocation_type: str = Field(pattern="^(per_call|monthly_amortized)$")
    value_usd: float = Field(ge=0)
    expected_monthly_calls: int | None = Field(default=None, gt=0)


def _effective_per_call_cost(allocation: dict[str, Any]) -> float | None:
    if allocation["allocation_type"] == "per_call":
        return allocation["value_usd"]
    calls = allocation.get("expected_monthly_calls")
    if not calls:
        return None
    return allocation["value_usd"] / calls


@router.get("/infra-allocations")
async def list_infra_allocations(request: Request, user: CurrentUser = Depends(require_admin)):
    del user
    db = _db(request)
    allocations = {}
    if db is not None:
        allocations = {
            doc["model"]: doc for doc in db["infra_cost_allocations"].find({}, {"_id": 0})
        }
    return {
        "models": [
            {
                "model": model,
                "allocation": allocations.get(model),
                "effective_usd_per_call": (
                    _effective_per_call_cost(allocations[model])
                    if model in allocations
                    else None
                ),
            }
            for model in _LOCAL_MODELS
        ]
    }


@router.put("/infra-allocations/{model}")
async def set_infra_allocation(
    model: str,
    body: InfraAllocationRequest,
    request: Request,
    user: CurrentUser = Depends(require_admin),
):
    if model not in _LOCAL_MODELS:
        raise HTTPException(
            status_code=404,
            detail=f"{model!r} is not a local model ({', '.join(_LOCAL_MODELS)}).",
        )
    if body.allocation_type == "monthly_amortized" and not body.expected_monthly_calls:
        raise HTTPException(
            status_code=422,
            detail="expected_monthly_calls is required for monthly_amortized allocations",
        )
    db = _db(request)
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    db["infra_cost_allocations"].update_one(
        {"model": model},
        {
            "$set": {
                "model": model,
                "allocation_type": body.allocation_type,
                "value_usd": body.value_usd,
                "expected_monthly_calls": body.expected_monthly_calls,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": user.username,
            }
        },
        upsert=True,
    )
    return {"model": model, "status": "set"}


def _allocated_infra_cost(entries: list[dict], allocations: dict[str, dict]) -> float:
    calls_by_model: dict[str, int] = defaultdict(int)
    for entry in entries:
        if entry.get("model") in allocations:
            calls_by_model[entry["model"]] += 1
    total = 0.0
    for model, calls in calls_by_model.items():
        per_call = _effective_per_call_cost(allocations[model])
        if per_call:
            total += per_call * calls
    return round(total, 6)


# ---------------------------------------------------------------------------
# Prompt-cache summary
# ---------------------------------------------------------------------------


def _cache_savings_usd(entry: dict) -> float:
    """Mirrors CostLedger.calculate()'s own cache multipliers — what THIS entry's cached
    tokens would have cost at full input price, minus what they actually cost."""
    model = entry.get("model") or ""
    p_in = MODEL_PRICING.get(model, (0.005, 0.015))[0]
    cache_read = entry.get("cache_read_input_tokens") or 0
    if not cache_read:
        return 0.0
    multiplier = ANTHROPIC_CACHE_READ_MULTIPLIER if model.startswith("claude") else OPENAI_CACHE_READ_MULTIPLIER
    full_cost = cache_read * p_in / 1000
    cached_cost = full_cost * multiplier
    return round(full_cost - cached_cost, 6)


@router.get("/cache-summary")
async def cache_summary(
    request: Request,
    since_days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(require_admin),
):
    del user
    db = _db(request)
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    entries = _cost_ledger_entries(db, since=since)

    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"cache_creation_tokens": 0, "cache_read_tokens": 0, "estimated_savings_usd": 0.0}
    )
    total_creation = 0
    total_read = 0
    total_savings = 0.0
    for entry in entries:
        creation = entry.get("cache_creation_input_tokens") or 0
        read = entry.get("cache_read_input_tokens") or 0
        if not creation and not read:
            continue
        savings = _cache_savings_usd(entry)
        model = entry.get("model") or "unknown"
        by_model[model]["cache_creation_tokens"] += creation
        by_model[model]["cache_read_tokens"] += read
        by_model[model]["estimated_savings_usd"] = round(
            by_model[model]["estimated_savings_usd"] + savings, 6
        )
        total_creation += creation
        total_read += read
        total_savings += savings

    return {
        "since_days": since_days,
        "total_cache_creation_tokens": total_creation,
        "total_cache_read_tokens": total_read,
        "estimated_total_savings_usd": round(total_savings, 6),
        "by_model": [{"model": model, **stats} for model, stats in sorted(by_model.items())],
    }


# ---------------------------------------------------------------------------
# Budget controls — LLM_USER_DAILY_BUDGET_USD/LLM_GLOBAL_DAILY_BUDGET_USD existed as
# unused env vars before this; this is the first real place they're read and enforceable
# (reporting only — this does not block requests; see app/api/cost_admin.py's module intent).
# ---------------------------------------------------------------------------


class BudgetRequest(BaseModel):
    daily_limit_usd: float = Field(ge=0)


def _today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/budgets")
async def get_budgets(request: Request, user: CurrentUser = Depends(require_admin)):
    del user
    db = _db(request)
    today_entries = _cost_ledger_entries(db, since=_today_start())
    spend_today = round(sum(e.get("cost_usd") or 0.0 for e in today_entries), 6)

    spend_by_session: dict[str, float] = defaultdict(float)
    for entry in today_entries:
        spend_by_session[entry.get("session_id") or "unknown"] += entry.get("cost_usd") or 0.0

    global_budget = None
    user_budgets: dict[str, float] = {}
    if db is not None:
        global_doc = db["budget_config"].find_one({"scope": "global"})
        if global_doc:
            global_budget = global_doc["daily_limit_usd"]
        user_budgets = {
            doc["session_id"]: doc["daily_limit_usd"]
            for doc in db["budget_config"].find({"scope": "session"})
        }

    return {
        "global": {
            "daily_limit_usd": global_budget,
            "spend_today_usd": spend_today,
            "exceeded": global_budget is not None and spend_today > global_budget,
        },
        "by_session": [
            {
                "session_id": session_id,
                "daily_limit_usd": user_budgets.get(session_id),
                "spend_today_usd": round(spend, 6),
                "exceeded": (
                    session_id in user_budgets and spend > user_budgets[session_id]
                ),
            }
            for session_id, spend in sorted(
                spend_by_session.items(), key=lambda item: item[1], reverse=True
            )
        ],
    }


@router.put("/budgets/global")
async def set_global_budget(
    body: BudgetRequest, request: Request, user: CurrentUser = Depends(require_admin)
):
    db = _db(request)
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    db["budget_config"].update_one(
        {"scope": "global"},
        {"$set": {
            "scope": "global",
            "daily_limit_usd": body.daily_limit_usd,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": user.username,
        }},
        upsert=True,
    )
    return {"scope": "global", "daily_limit_usd": body.daily_limit_usd}


@router.put("/budgets/session/{session_id}")
async def set_session_budget(
    session_id: str,
    body: BudgetRequest,
    request: Request,
    user: CurrentUser = Depends(require_admin),
):
    db = _db(request)
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    db["budget_config"].update_one(
        {"scope": "session", "session_id": session_id},
        {"$set": {
            "scope": "session",
            "session_id": session_id,
            "daily_limit_usd": body.daily_limit_usd,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": user.username,
        }},
        upsert=True,
    )
    return {"session_id": session_id, "daily_limit_usd": body.daily_limit_usd}


# ---------------------------------------------------------------------------
# Account-wide overview — the admin page's landing tab.
# ---------------------------------------------------------------------------


@router.get("/overview")
async def cost_overview(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(require_admin),
):
    del user
    db = _db(request)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    entries = _cost_ledger_entries(db, since=since)

    total_usd = round(sum(e.get("cost_usd") or 0.0 for e in entries), 6)

    daily: dict[str, float] = defaultdict(float)
    by_model: dict[str, float] = defaultdict(float)
    by_provider: dict[str, float] = defaultdict(float)
    by_collection: dict[str, float] = defaultdict(float)
    run_ids: set[str] = set()
    for entry in entries:
        cost = entry.get("cost_usd") or 0.0
        ts = entry.get("ts")
        day_key = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else "unknown"
        daily[day_key] += cost
        by_model[entry.get("model") or "unknown"] += cost
        by_provider[entry.get("provider") or "unknown"] += cost
        by_collection[entry.get("collection_id") or "default"] += cost
        if entry.get("run_id"):
            run_ids.add(entry["run_id"])

    # Per-workflow: joins against run_history (same "db" connection, different collection —
    # see module docstring) since LedgerEntry only carries run_id, not workflow_name.
    by_workflow: dict[str, float] = defaultdict(float)
    if db is not None and run_ids:
        run_to_workflow = {
            doc["run_id"]: doc.get("workflow_name") or "unknown"
            for doc in db["run_history"].find(
                {"run_id": {"$in": list(run_ids)}}, {"_id": 0, "run_id": 1, "workflow_name": 1}
            )
        }
        for entry in entries:
            workflow_name = run_to_workflow.get(entry.get("run_id"), "unknown")
            by_workflow[workflow_name] += entry.get("cost_usd") or 0.0

    allocations = {}
    if db is not None:
        allocations = {
            doc["model"]: doc for doc in db["infra_cost_allocations"].find({}, {"_id": 0})
        }
    allocated_infra_usd = _allocated_infra_cost(entries, allocations)

    def _sorted_breakdown(d: dict[str, float]) -> list[dict[str, Any]]:
        return [
            {"label": label, "cost_usd": round(cost, 6)}
            for label, cost in sorted(d.items(), key=lambda item: item[1], reverse=True)
        ]

    return {
        "days": days,
        "total_usd": total_usd,
        "allocated_infra_usd": allocated_infra_usd,
        "call_count": len(entries),
        "daily_trend": [
            {"date": date, "cost_usd": round(cost, 6)}
            for date, cost in sorted(daily.items())
            if date != "unknown"
        ],
        "by_model": _sorted_breakdown(by_model),
        "by_provider": _sorted_breakdown(by_provider),
        "by_collection": _sorted_breakdown(by_collection),
        "by_workflow": _sorted_breakdown(by_workflow),
    }
